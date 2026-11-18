import os
import subprocess
import unittest


def banner(tool):
    for args in (["--version"], ["-W", "version"]):
        try:
            out = subprocess.run([tool, *args], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL)
        except OSError:
            return ""
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.splitlines()[0]
    return ""


class ToolFlavourTests(unittest.TestCase):
    """CI names the awk and grep it runs the gate tests under; a runner image swap fails here."""

    def check(self, tool):
        want = os.environ.get("RIGOPS_TEST_EXPECT_" + tool.upper())
        if not want:
            self.skipTest("RIGOPS_TEST_EXPECT_" + tool.upper() + " not set")
        self.assertIn(want, banner(tool))

    def test_awk_is_the_named_flavour(self):
        self.check("awk")

    def test_grep_is_the_named_flavour(self):
        self.check("grep")


if __name__ == "__main__":
    unittest.main()
