"""Check how ci_status maps GitHub run results and reads remote URLs."""

from __future__ import annotations

import unittest

from tools.ci_status import Run, parse_remote


class StateTests(unittest.TestCase):
    def test_running_is_pending(self) -> None:
        self.assertEqual(Run("x", "in_progress", None, "").gitea_state, "pending")

    def test_success_and_skipped_pass(self) -> None:
        self.assertEqual(Run("x", "completed", "success", "").gitea_state, "success")
        self.assertEqual(Run("x", "completed", "skipped", "").gitea_state, "success")

    def test_failure_timeout_and_cancel_fail(self) -> None:
        for conclusion in ("failure", "timed_out", "cancelled", "startup_failure"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(Run("x", "completed", conclusion, "").gitea_state, "failure")

    def test_unknown_conclusion_is_an_error_not_a_pass(self) -> None:
        self.assertEqual(Run("x", "completed", "action_required", "").gitea_state, "error")


class RemoteTests(unittest.TestCase):
    def test_gitea_http_remote(self) -> None:
        base, repo = parse_remote("http://192.168.40.80:3000/vanviet/esp-sr.git")
        self.assertEqual((base, repo), ("http://192.168.40.80:3000", "vanviet/esp-sr"))

    def test_credentials_in_a_remote_are_dropped(self) -> None:
        base, repo = parse_remote("https://user:secret@github.com/owner/esp-sr.git")
        self.assertEqual((base, repo), ("https://github.com", "owner/esp-sr"))
        self.assertNotIn("secret", base)

    def test_remote_without_dot_git(self) -> None:
        self.assertEqual(parse_remote("https://github.com/owner/esp-sr")[1], "owner/esp-sr")

    def test_ssh_remote_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            parse_remote("git@github.com:owner/esp-sr.git")


if __name__ == "__main__":
    unittest.main()
