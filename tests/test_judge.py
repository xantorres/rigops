from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from rigops.judge import ACTIONS, STATUSES, judge_job  # noqa: E402

# (name, runtime_h, max_h, last_exit_status, evidence_age_h, expected_interval_h,
#  heal_cooldown_active, expected_status, expected_action)
CASES = [
    ("hung_over_ceiling", 3.0, 2.0, None, None, None, False, "hung", "kill"),
    ("hung_ignores_cooldown_and_other_signals", 3.0, 2.0, 1, 999.0, 24.0, True, "hung", "kill"),
    ("running_at_ceiling_is_ok_not_hung", 2.0, 2.0, None, None, None, False, "ok", "none"),
    ("running_under_ceiling_is_ok", 1.0, 2.0, None, None, None, False, "ok", "none"),
    ("failing_exit_nonzero_kickstarts", None, 6.0, 1, None, None, False, "failing", "kickstart"),
    ("failing_exit_negative_kickstarts", None, 6.0, -1, None, None, False, "failing", "kickstart"),
    ("failing_notifies_in_cooldown", None, 6.0, 1, None, None, True, "failing", "notify"),
    (
        "stale_evidence_past_threshold_kickstarts",
        None, 6.0, 0, 37.0, 24.0, False, "stale", "kickstart",
    ),
    ("stale_notifies_in_cooldown", None, 6.0, 0, 37.0, 24.0, True, "stale", "notify"),
    (
        "stale_boundary_exactly_1_5x_interval_is_not_stale",
        None, 6.0, 0, 36.0, 24.0, False, "ok", "none",
    ),
    ("stale_just_past_boundary_is_stale", None, 6.0, 0, 36.01, 24.0, False, "stale", "kickstart"),
    (
        "exit_fail_wins_over_stale_when_both_true",
        None, 6.0, 1, 999.0, 24.0, False, "failing", "kickstart",
    ),
    (
        "no_evidence_yet_is_unknown_not_stale",
        None, 6.0, None, None, 24.0, False, "unknown", "none",
    ),
    (
        "ondemand_cadence_never_stale_regardless_of_age",
        None, 6.0, 0, 100000.0, None, False, "ok", "none",
    ),
    ("never_run_no_evidence_is_unknown", None, 6.0, None, None, None, False, "unknown", "none"),
    ("never_run_but_evidence_fresh_is_ok", None, 6.0, None, 1.0, 24.0, False, "ok", "none"),
    ("exit_zero_no_cadence_is_ok", None, 6.0, 0, None, None, False, "ok", "none"),
]


class JudgeJobDecisionTableTests(unittest.TestCase):
    def test_decision_table(self):
        for (
            name, runtime_h, max_h, last_exit, evidence_age_h, interval_h, cooldown,
            expected_status, expected_action,
        ) in CASES:
            with self.subTest(name):
                status, action = judge_job(
                    runtime_h=runtime_h, max_runtime_h=max_h, last_exit_status=last_exit,
                    evidence_age_h=evidence_age_h, expected_interval_h=interval_h,
                    heal_cooldown_active=cooldown,
                )
                self.assertEqual((status, action), (expected_status, expected_action))

    def test_every_status_reachable(self):
        statuses = {c[7] for c in CASES}
        self.assertEqual(statuses, set(STATUSES))

    def test_every_action_reachable(self):
        actions = {c[8] for c in CASES}
        self.assertEqual(actions, set(ACTIONS))


if __name__ == "__main__":
    unittest.main()
