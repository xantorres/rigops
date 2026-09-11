from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops import state  # noqa: E402
from rigops.eval import client  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_SCRIPT = REPO_ROOT / "libexec" / "rigops-eval"


def _run_eval(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), *args],
        env=env, capture_output=True, text=True, check=False,
    )


def _write_case(root: Path, name: str, **overrides) -> Path:
    data = {"group": "g", "prompt": "say hi", "expect": {"contains": ["hi"]}}
    data.update(overrides)
    path = root / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


def _handler_factory(responder):
    """Build a handler that records each request and replies via
    ``responder(payload) -> (status, raw_bytes)``."""
    captured: list = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except ValueError:
                payload = None
            captured.append({"path": self.path, "headers": self.headers, "payload": payload})
            status, body = responder(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    return Handler, captured


def _content_reply(pick):
    """Wrap ``pick(payload) -> text`` into a 200 ``{"choices": [...]}`` responder."""

    def responder(payload):
        body = json.dumps({"choices": [{"message": {"content": pick(payload)}}]})
        return 200, body.encode("utf-8")

    return responder


def _marker_reply(mapping: dict, default: str = "ok"):
    """Reply based on a marker substring found in the last message's content."""

    def pick(payload):
        message = payload["messages"][-1]["content"]
        for marker, reply in mapping.items():
            if marker in message:
                return reply
        return default

    return _content_reply(pick)


class _StubServerMixin:
    def _start_stub(self, responder):
        handler_cls, captured = _handler_factory(responder)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}/v1", captured


class _EnvIsolationMixin:
    def setUp(self):
        super().setUp()
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        super().tearDown()

    def _env_for(self, tmp: str) -> dict:
        env = dict(os.environ)
        env["RIGOPS_CONFIG"] = str(Path(tmp) / "config.json")
        env["RIGOPS_STATE_DIR"] = str(Path(tmp) / "state")
        env["XDG_STATE_HOME"] = str(Path(tmp) / "xdg-state")
        return env


class ChatUrlTests(unittest.TestCase):
    CASES = [
        ("plain_base", "http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/chat/completions"),
        ("trailing_slash", "http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1/chat/completions"),
        (
            "already_full_path",
            "http://127.0.0.1:1234/v1/chat/completions",
            "http://127.0.0.1:1234/v1/chat/completions",
        ),
        (
            "already_full_path_trailing_slash",
            "http://127.0.0.1:1234/v1/chat/completions/",
            "http://127.0.0.1:1234/v1/chat/completions",
        ),
    ]

    def test_table(self):
        for name, endpoint, expected in self.CASES:
            with self.subTest(name):
                self.assertEqual(client.chat_url(endpoint), expected)


class ClientCompleteTests(_StubServerMixin, unittest.TestCase):
    def test_payload_shape_and_no_auth_header_without_api_key(self):
        endpoint, captured = self._start_stub(_content_reply(lambda payload: "hello"))
        result = client.complete(
            endpoint, "test-model", [{"role": "user", "content": "hi"}],
            max_tokens=64, temperature=0.2, timeout_s=5,
        )
        self.assertIsNone(result["error"])
        self.assertEqual(result["content"], "hello")
        self.assertIsInstance(result["ms"], int)
        request = captured[0]
        self.assertEqual(request["payload"]["model"], "test-model")
        self.assertEqual(request["payload"]["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(request["payload"]["temperature"], 0.2)
        self.assertEqual(request["payload"]["max_tokens"], 64)
        self.assertIs(request["payload"]["stream"], False)
        self.assertIsNone(request["headers"].get("Authorization"))

    def test_api_key_sent_as_bearer_header(self):
        endpoint, captured = self._start_stub(_content_reply(lambda payload: "hello"))
        client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key="k",
        )
        self.assertEqual(captured[0]["headers"].get("Authorization"), "Bearer k")

    def test_leading_think_block_stripped(self):
        endpoint, _ = self._start_stub(
            _content_reply(lambda payload: "<think>scratch</think>final answer")
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(result["content"], "final answer")
        self.assertIsNone(result["error"])

    def test_http_500_with_body_sets_error(self):
        endpoint, _ = self._start_stub(lambda payload: (500, b'{"error": "boom"}'))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertTrue(result["error"].startswith("HTTP 500"))

    def test_http_error_redacts_bearer_token_in_body(self):
        api_key = "sk-test-abc123"
        endpoint, _ = self._start_stub(
            lambda payload: (401, f"unauthorized: Bearer {api_key} is invalid".encode())
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key=api_key,
        )
        self.assertIn("[redacted]", result["error"])
        self.assertNotIn(api_key, result["error"])

    def test_http_error_redacts_token_straddling_200_char_truncation(self):
        api_key = "sk-boundary-secret-value-9999"
        pad = "x" * 182
        body = f"{pad} Bearer {api_key} trailing padding after the secret key here"
        endpoint, _ = self._start_stub(lambda payload: (401, body.encode()))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key=api_key,
        )
        self.assertIn("[redacted]", result["error"])
        self.assertNotIn(api_key, result["error"])
        self.assertNotIn(api_key[:8], result["error"])

    def test_http_error_body_whitespace_collapsed_to_single_line(self):
        endpoint, _ = self._start_stub(
            lambda payload: (500, b"line one\nline two\tline three\r\nline four")
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertNotIn("\n", result["error"])
        self.assertNotIn("\t", result["error"])
        self.assertNotIn("\r", result["error"])

    def test_non_json_response_body_returns_error_without_raising(self):
        endpoint, _ = self._start_stub(lambda payload: (200, b"not json at all"))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertIsNotNone(result["error"])

    def test_missing_choices_key(self):
        endpoint, _ = self._start_stub(lambda payload: (200, b"{}"))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(result["error"], "reply has no choices[0].message.content")

    def test_null_content_is_empty_string_no_error(self):
        body = json.dumps({"choices": [{"message": {"content": None}}]}).encode("utf-8")
        endpoint, _ = self._start_stub(lambda payload: (200, body))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(result["content"], "")
        self.assertIsNone(result["error"])

    def test_numeric_content_is_not_a_string_error(self):
        body = json.dumps({"choices": [{"message": {"content": 42}}]}).encode("utf-8")
        endpoint, _ = self._start_stub(lambda payload: (200, body))
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(result["error"], "choices[0].message.content is not a string")

    def test_closed_port_is_unreachable(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        result = client.complete(
            f"http://127.0.0.1:{port}/v1", "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertTrue(result["error"].startswith("unreachable"))

    def test_redirect_refused_and_target_receives_no_requests(self):
        target_endpoint, target_captured = self._start_stub(
            _content_reply(lambda payload: "should not be fetched")
        )

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)
                self.send_response(302)
                self.send_header("Location", client.chat_url(target_endpoint))
                self.end_headers()

        redirect_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        threading.Thread(
            target=redirect_server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        self.addCleanup(redirect_server.shutdown)
        self.addCleanup(redirect_server.server_close)
        redirect_endpoint = f"http://127.0.0.1:{redirect_server.server_address[1]}/v1"

        result = client.complete(
            redirect_endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertTrue(result["error"].startswith("HTTP 302"))
        self.assertEqual(target_captured, [])

    def test_file_endpoint_rejected_without_network_call(self):
        result = client.complete(
            "file:///etc/passwd", "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(result["error"], "endpoint must be an http or https URL")

    def test_reply_over_patched_max_bytes(self):
        endpoint, _ = self._start_stub(_content_reply(lambda payload: "x" * 100))
        with patch.object(client, "MAX_REPLY_BYTES", 5):
            result = client.complete(
                endpoint, "m", [{"role": "user", "content": "hi"}],
                max_tokens=10, temperature=0, timeout_s=5,
            )
        self.assertTrue(result["error"].startswith("reply larger than"))

    def test_slow_trickle_times_out_before_body_completes(self):
        class TrickleHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    self.rfile.read(length)
                body = json.dumps({"choices": [{"message": {"content": "x" * 40}}]}).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    deadline = time.monotonic() + 1.5
                    for byte in body:
                        if time.monotonic() > deadline:
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TrickleHandler)
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1"

        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=0.3,
        )
        self.assertEqual(result["error"], "timed out")

    def test_api_key_with_carriage_return_never_appears_in_error(self):
        api_key = "sec\rret-key-value"
        result = client.complete(
            "http://127.0.0.1:9/v1", "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key=api_key,
        )
        self.assertIsNotNone(result["error"])
        self.assertNotIn(api_key, result["error"])
        self.assertNotIn("\r", result["error"])

    def test_401_body_echoing_key_upper_case_is_withheld(self):
        api_key = "sk-test-secret-value"
        endpoint, _ = self._start_stub(
            lambda payload: (401, f"unauthorized token {api_key.upper()} rejected".encode())
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key=api_key,
        )
        self.assertIn("detail withheld", result["error"])
        lowered = result["error"].lower()
        key_lower = api_key.lower()
        for i in range(len(key_lower) - 7):
            self.assertNotIn(key_lower[i:i + 8], lowered)

    def test_401_body_echoing_first_12_chars_is_withheld(self):
        api_key = "sk-test-secret-value"
        endpoint, _ = self._start_stub(
            lambda payload: (401, f"unauthorized token {api_key[:12]} rejected".encode())
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key=api_key,
        )
        self.assertIn("detail withheld", result["error"])

    def test_unterminated_leading_think_block(self):
        endpoint, _ = self._start_stub(
            _content_reply(lambda payload: "<think>never closes at all")
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertEqual(
            result["error"], "reply ended inside a <think> block; raise max_tokens"
        )

    def test_error_body_escape_sequence_never_reaches_error_text(self):
        endpoint, _ = self._start_stub(
            lambda payload: (500, b"server exploded \x1b[8mhidden\x1b[0m text")
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5,
        )
        self.assertNotIn("\x1b", result["error"])

    def test_short_api_key_keeps_the_detail_and_redacts_only_the_literal_key(self):
        # A key under 8 chars skips the window check, so the rest of the detail survives;
        # a literal match is still redacted, since it could be the key echoed back.
        endpoint, _ = self._start_stub(
            lambda payload: (400, b'{"error": "messages must not be empty"}')
        )
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=5, api_key="empty",
        )
        self.assertIn("HTTP 400", result["error"])
        self.assertIn("messages must not be [redacted]", result["error"])
        self.assertNotIn("detail withheld", result["error"])

    def test_whole_call_deadline_trips_on_slow_trickled_status_line(self):
        class TrickleHeaderHandler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.request.settimeout(1)
                    self.request.recv(65536)
                except OSError:
                    pass
                body = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 2\r\n\r\n{}"
                )
                deadline = time.monotonic() + 2.0
                for byte in body:
                    if time.monotonic() > deadline:
                        break
                    try:
                        self.request.sendall(bytes([byte]))
                    except OSError:
                        break
                    time.sleep(0.05)

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TrickleHeaderHandler)
        server.daemon_threads = True
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1"
        timeout_s = 0.4

        started = time.monotonic()
        result = client.complete(
            endpoint, "m", [{"role": "user", "content": "hi"}],
            max_tokens=10, temperature=0, timeout_s=timeout_s,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result["error"], "timed out")
        self.assertLess(elapsed, timeout_s + 1.0)


class EvalRunValidationTests(_EnvIsolationMixin, _StubServerMixin, unittest.TestCase):
    def test_missing_model_no_config_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1"], env
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("eval.model", result.stderr)

    def test_broken_case_file_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            (cases_dir / "broken.json").write_text("not valid json")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("broken.json", result.stderr)

    def test_non_http_endpoint_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "ftp://nope", "--model", "m"], env
            )
            self.assertEqual(result.returncode, 2)

    def test_temperature_nan_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--temperature", "nan"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("--temperature must be a finite number", result.stderr)

    def test_temperature_negative_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--temperature", "-1"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("--temperature must be a finite number", result.stderr)

    def test_api_key_env_unset_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            env.pop("RIGOPS_EVAL_TEST_KEY_UNSET", None)
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--api-key-env", "RIGOPS_EVAL_TEST_KEY_UNSET"],
                env,
            )
            self.assertEqual(result.returncode, 2)

    def test_diff_negative_latency_tolerance_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            for label in ("first", "second"):
                endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
                seed = _run_eval(
                    ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                     "--label", label],
                    env,
                )
                self.assertEqual(seed.returncode, 0)
            result = _run_eval(["diff", "--latency-tolerance", "-1"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("--latency-tolerance must be zero or more", result.stderr)

    def test_system_file_non_utf8_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            system_path = Path(tmp) / "system.md"
            system_path.write_bytes(b"\xff\xfe\xfa")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--system", str(system_path)],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("cannot read system prompt", result.stderr)

    def test_invalid_config_json_run_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            Path(env["RIGOPS_CONFIG"]).parent.mkdir(parents=True, exist_ok=True)
            Path(env["RIGOPS_CONFIG"]).write_text("{not json")
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m"],
                env,
            )
            self.assertEqual(result.returncode, 2)

    def test_diff_blank_base_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_eval(["diff", "--base", ""], env)
            self.assertEqual(result.returncode, 2)

    def test_diff_latency_tolerance_nan_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_eval(["diff", "--latency-tolerance", "nan"], env)
            self.assertEqual(result.returncode, 2)

    def test_endpoint_with_credentials_rejected_and_password_not_leaked(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir),
                 "--endpoint", "http://user:pw123@127.0.0.1:9/v1", "--model", "m"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("pw123", result.stderr)

    def test_endpoint_with_query_string_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir),
                 "--endpoint", "http://127.0.0.1:9/v1?x=1", "--model", "m"],
                env,
            )
            self.assertEqual(result.returncode, 2)

    def test_api_key_env_flag_holding_a_pasted_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--api-key-env", "sk-live-pasted-x"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("sk-live-pasted-x", result.stderr)

    def test_api_key_env_value_with_control_char_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            env["RIGOPS_EVAL_TEST_CRLF"] = "abc\r"
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--api-key-env", "RIGOPS_EVAL_TEST_CRLF"],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("whitespace or control characters", result.stderr)

    def test_config_endpoint_as_list_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            Path(env["RIGOPS_CONFIG"]).parent.mkdir(parents=True, exist_ok=True)
            Path(env["RIGOPS_CONFIG"]).write_text(json.dumps({"eval": {"endpoint": ["a", "b"]}}))
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            result = _run_eval(["run", "--cases", str(cases_dir), "--model", "m"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("eval.endpoint in config must be a string", result.stderr)

    def test_system_prompt_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "c1")
            real_system = Path(tmp) / "real_system.md"
            real_system.write_text("You are terse.")
            link_system = Path(tmp) / "link_system.md"
            os.symlink(real_system, link_system)
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", "http://127.0.0.1:9/v1",
                 "--model", "m", "--system", str(link_system)],
                env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("cannot read system prompt", result.stderr)


class EvalRunAgainstStubTests(_EnvIsolationMixin, _StubServerMixin, unittest.TestCase):
    def test_run_records_row_with_expected_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", prompt="say ok", expect={"contains": ["ok"]})
            _write_case(cases_dir, "beta", prompt="say ok too", expect={"contains": ["ok"]})
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "ok"))

            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("[1/2]", result.stderr)
            self.assertIn("[2/2]", result.stderr)
            self.assertIn("total", result.stdout)

            rows = state.read_jsonl(Path(tmp) / "state" / "eval.jsonl")
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertTrue(row["run_id"])
            self.assertEqual(row["label"], "main")
            self.assertEqual(row["model"], "m")
            self.assertIsInstance(row["suite"], str)
            self.assertEqual(row["totals"]["total"], 2)
            self.assertEqual(row["totals"]["passed"], 2)
            self.assertIn("g", row["groups"])
            for case_id in ("alpha", "beta"):
                entry = row["cases"][case_id]
                self.assertEqual(entry["status"], "pass")
                self.assertIsInstance(entry["ms"], int)
                self.assertEqual(entry["reason"], "")
                self.assertEqual(len(entry["hash"]), 12)

    def test_case_max_tokens_forwarded_in_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", max_tokens=77)
            endpoint, captured = self._start_stub(_content_reply(lambda payload: "hi"))
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m"], env
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(captured[0]["payload"]["max_tokens"], 77)

    def test_dry_run_does_not_write_eval_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--dry-run"],
                env,
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse((Path(tmp) / "state" / "eval.jsonl").exists())

    def test_json_flag_stdout_parses_as_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--json"],
                env,
            )
            self.assertEqual(result.returncode, 0)
            row = json.loads(result.stdout)
            self.assertEqual(row["totals"]["total"], 1)
            self.assertIn("alpha", row["cases"])

    def test_system_file_sent_as_first_message_and_row_system_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            system_path = Path(tmp) / "system.md"
            system_path.write_text("You are a terse assistant.")
            endpoint, captured = self._start_stub(_content_reply(lambda payload: "hi"))

            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--system", str(system_path)],
                env,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(captured[0]["payload"]["messages"][0]["role"], "system")
            self.assertEqual(
                captured[0]["payload"]["messages"][0]["content"], "You are a terse assistant."
            )

            rows = state.read_jsonl(Path(tmp) / "state" / "eval.jsonl")
            self.assertRegex(rows[0]["system"], r"^[0-9a-f]{12}$")

    def test_failing_reason_escape_byte_not_in_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", expect={"json": {"a\x1bb": "x"}})
            endpoint, _ = self._start_stub(_content_reply(lambda payload: '{"other": 1}'))
            result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m"], env
            )
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("\x1b", result.stdout)


class EvalDiffTests(_EnvIsolationMixin, _StubServerMixin, unittest.TestCase):
    def _seed_main_then_regressed_proposal(self, tmp, env):
        cases_dir = Path(tmp) / "cases"
        cases_dir.mkdir()
        _write_case(cases_dir, "alpha", group="g1", prompt="reply ALPHA",
                    expect={"contains": ["alpha-ok"]})
        _write_case(cases_dir, "beta", group="g1", prompt="reply BETA",
                    expect={"contains": ["beta-ok"]})
        main_endpoint, _ = self._start_stub(
            _marker_reply({"ALPHA": "alpha-ok", "BETA": "beta-ok"})
        )
        main_result = _run_eval(
            ["run", "--cases", str(cases_dir), "--endpoint", main_endpoint, "--model", "m",
             "--label", "main"],
            env,
        )
        self.assertEqual(main_result.returncode, 0)
        proposal_endpoint, _ = self._start_stub(
            _marker_reply({"ALPHA": "alpha-ok", "BETA": "beta-WRONG"})
        )
        proposal_result = _run_eval(
            ["run", "--cases", str(cases_dir), "--endpoint", proposal_endpoint, "--model", "m",
             "--label", "proposal"],
            env,
        )
        self.assertEqual(proposal_result.returncode, 0)
        return cases_dir

    def test_default_base_head_reports_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_main_then_regressed_proposal(tmp, env)
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("REGRESSION g1", result.stdout)

    def test_regression_line_exact_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_main_then_regressed_proposal(tmp, env)
            result = _run_eval(["diff"], env)
            self.assertIn("REGRESSION g1: 1 newly failing, 2/2 -> 1/2 passing", result.stdout)

    def test_diff_all_unverified_exits_two_with_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", prompt="say ok", expect={"contains": ["ok"]})
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]
            sock.close()
            base_result = _run_eval(
                ["run", "--cases", str(cases_dir),
                 "--endpoint", f"http://127.0.0.1:{closed_port}/v1", "--model", "m",
                 "--label", "main"],
                env,
            )
            self.assertEqual(base_result.returncode, 0)
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "nope"))
            head_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "proposal"],
                env,
            )
            self.assertEqual(head_result.returncode, 0)
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("UNVERIFIED", result.stdout)

    def test_diff_regression_plus_unverified_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", group="g1", prompt="reply ALPHA",
                        expect={"contains": ["alpha-ok"]})
            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA",
                        expect={"contains": ["beta-ok"]})

            def base_responder(payload):
                message = payload["messages"][-1]["content"]
                if "ALPHA" in message:
                    body = json.dumps({"choices": [{"message": {"content": "alpha-ok"}}]})
                    return 200, body.encode()
                return 500, b'{"error": "boom"}'

            def head_responder(payload):
                message = payload["messages"][-1]["content"]
                content = "alpha-WRONG" if "ALPHA" in message else "beta-WRONG"
                body = json.dumps({"choices": [{"message": {"content": content}}]})
                return 200, body.encode()

            base_endpoint, _ = self._start_stub(base_responder)
            base_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", base_endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )
            self.assertEqual(base_result.returncode, 0)
            head_endpoint, _ = self._start_stub(head_responder)
            head_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", head_endpoint, "--model", "m",
                 "--label", "proposal"],
                env,
            )
            self.assertEqual(head_result.returncode, 0)

            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("REGRESSION", result.stdout)
            self.assertIn("UNVERIFIED", result.stdout)

    def test_json_flag_exits_one_and_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_main_then_regressed_proposal(tmp, env)
            result = _run_eval(["diff", "--json"], env)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertTrue(report["regressions"])

    def test_explicit_base_and_head_by_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            self._seed_main_then_regressed_proposal(tmp, env)
            result = _run_eval(["diff", "--base", "main", "--head", "proposal"], env)
            self.assertEqual(result.returncode, 1)
            self.assertIn("REGRESSION g1", result.stdout)

    def test_two_identical_outcome_runs_have_no_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", prompt="reply ALPHA",
                        expect={"contains": ["alpha-ok"]})
            responder = _marker_reply({"ALPHA": "alpha-ok"})
            for label in ("first", "second"):
                endpoint, _ = self._start_stub(responder)
                result = _run_eval(
                    ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                     "--label", label],
                    env,
                )
                self.assertEqual(result.returncode, 0)

            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("no regressions", result.stdout)

    def test_zero_runs_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)

    def test_one_run_exits_two_no_run_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m"], env
            )
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("no run before", result.stderr)

    def test_base_and_head_same_run_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "only"],
                env,
            )
            result = _run_eval(["diff", "--base", "only", "--head", "only"], env)
            self.assertEqual(result.returncode, 2)

    def test_edited_case_shows_changed_and_still_compares_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", group="g1", prompt="reply ALPHA",
                        expect={"contains": ["alpha-ok"]})
            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA",
                        expect={"contains": ["beta-ok"]})
            responder = _marker_reply({"ALPHA": "alpha-ok", "BETA": "beta-ok"})
            endpoint, _ = self._start_stub(responder)
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )

            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA now edited",
                        expect={"contains": ["beta-ok"]})
            endpoint2, _ = self._start_stub(responder)
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint2, "--model", "m",
                 "--label", "proposal"],
                env,
            )

            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("changed (not compared): beta", result.stdout)
            self.assertIn("compared 1 unchanged cases", result.stdout)
            self.assertIn("DROPPED 1 cases", result.stdout)

    def test_removed_passing_case_shows_dropped_and_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", group="g1", prompt="reply ALPHA",
                        expect={"contains": ["alpha-ok"]})
            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA",
                        expect={"contains": ["beta-ok"]})
            responder = _marker_reply({"ALPHA": "alpha-ok", "BETA": "beta-ok"})
            endpoint, _ = self._start_stub(responder)
            base_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )
            self.assertEqual(base_result.returncode, 0)

            (cases_dir / "beta.json").unlink()
            endpoint2, _ = self._start_stub(responder)
            head_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint2, "--model", "m",
                 "--label", "proposal"],
                env,
            )
            self.assertEqual(head_result.returncode, 0)

            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("DROPPED", result.stdout)

    def test_edited_failing_case_not_dropped_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha", group="g1", prompt="reply ALPHA",
                        expect={"contains": ["alpha-ok"]})
            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA",
                        expect={"contains": ["beta-ok"]})
            responder = _marker_reply({"ALPHA": "alpha-ok", "BETA": "totally-wrong"})
            endpoint, _ = self._start_stub(responder)
            base_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )
            self.assertEqual(base_result.returncode, 0)

            _write_case(cases_dir, "beta", group="g1", prompt="reply BETA now edited",
                        expect={"contains": ["beta-ok"]})
            endpoint2, _ = self._start_stub(responder)
            head_result = _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint2, "--model", "m",
                 "--label", "proposal"],
                env,
            )
            self.assertEqual(head_result.returncode, 0)

            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 0)
            self.assertNotIn("DROPPED", result.stdout)

    def test_disjoint_case_ids_share_no_unchanged_cases_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                 "--label", "main"],
                env,
            )
            (cases_dir / "alpha.json").unlink()
            _write_case(cases_dir, "gamma")
            endpoint2, _ = self._start_stub(_content_reply(lambda payload: "hi"))
            _run_eval(
                ["run", "--cases", str(cases_dir), "--endpoint", endpoint2, "--model", "m",
                 "--label", "proposal"],
                env,
            )
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("share no unchanged cases", result.stderr)

    def test_three_runs_default_diff_compares_last_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            cases_dir = Path(tmp) / "cases"
            cases_dir.mkdir()
            _write_case(cases_dir, "alpha")
            run_ids = {}
            for label in ("r1", "r2", "r3"):
                endpoint, _ = self._start_stub(_content_reply(lambda payload: "hi"))
                result = _run_eval(
                    ["run", "--cases", str(cases_dir), "--endpoint", endpoint, "--model", "m",
                     "--label", label, "--json"],
                    env,
                )
                self.assertEqual(result.returncode, 0)
                run_ids[label] = json.loads(result.stdout)["run_id"]

            result = _run_eval(["diff", "--json"], env)
            self.assertEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report["base"]["run_id"], run_ids["r2"])
            self.assertEqual(report["head"]["run_id"], run_ids["r3"])

            result = _run_eval(["diff", "--head", "r2", "--json"], env)
            self.assertEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report["base"]["run_id"], run_ids["r1"])
            self.assertEqual(report["head"]["run_id"], run_ids["r2"])


class EvalMalformedStateTests(_EnvIsolationMixin, unittest.TestCase):
    def test_null_totals_row_diff_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "eval.jsonl", {"run_id": "r1", "totals": None, "cases": {}}
            )
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("malformed eval row", result.stderr)

    def test_null_totals_row_list_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "eval.jsonl", {"run_id": "r1", "totals": None, "cases": {}}
            )
            result = _run_eval(["list"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("malformed eval row", result.stderr)

    def test_non_json_line_diff_exits_two_not_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True)
            (state_dir / "eval.jsonl").write_text("not json at all\n")
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)

    def test_case_status_upper_case_row_diff_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "eval.jsonl",
                {
                    "run_id": "r1",
                    "totals": {"passed": 1, "total": 1, "errors": 0, "pass_rate": 1.0,
                               "p50_ms": 1, "p95_ms": 1},
                    "cases": {"c1": {"group": "g", "status": "PASS", "ms": 1, "hash": "h1"}},
                },
            )
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("malformed eval row", result.stderr)

    def test_state_dir_pointing_at_regular_file_diff_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state_as_file = Path(tmp) / "state_as_file"
            state_as_file.write_text("not a directory")
            env["RIGOPS_STATE_DIR"] = str(state_as_file)
            result = _run_eval(["diff"], env)
            self.assertEqual(result.returncode, 2)


class EvalListTests(_EnvIsolationMixin, unittest.TestCase):
    def test_shows_run_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "eval.jsonl",
                {
                    "run_id": "20260101-000000-aaaa", "label": "main", "model": "m", "suite": "s",
                    "totals": {"passed": 1, "total": 1, "errors": 0, "pass_rate": 1.0,
                               "p50_ms": 10, "p95_ms": 10},
                    "groups": {}, "cases": {},
                },
            )
            result = _run_eval(["list"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("20260101-000000-aaaa", result.stdout)

    def test_json_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            state.append_jsonl(
                Path(tmp) / "state" / "eval.jsonl",
                {
                    "run_id": "r1", "label": "", "model": "m", "suite": "s",
                    "totals": {"passed": 0, "total": 0, "errors": 0, "pass_rate": None,
                               "p50_ms": None, "p95_ms": None},
                    "groups": {}, "cases": {},
                },
            )
            result = _run_eval(["list", "--json"], env)
            self.assertEqual(result.returncode, 0)
            rows = json.loads(result.stdout)
            self.assertEqual(rows[0]["run_id"], "r1")

    def test_empty_prints_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env_for(tmp)
            result = _run_eval(["list"], env)
            self.assertEqual(result.returncode, 0)
            self.assertIn("no eval runs recorded yet", result.stdout)


if __name__ == "__main__":
    unittest.main()
