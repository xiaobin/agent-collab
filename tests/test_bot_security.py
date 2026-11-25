#!/usr/bin/env python3
"""Regression tests for independent stage-one review findings."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"
SCRIPT = ROOT / "scripts" / "bot.py"


class BotSecurityRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.definition = self.project / ".herdr" / "bots" / "test-bot" / "definition.json"
        self.policy_path = self.project / ".herdr" / "bot-policy.json"
        self.approved = self.project / ".herdr" / "bots" / "approved" / "test-bot" / "1.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def cli(self, *args: str, project: Path | None = None) -> subprocess.CompletedProcess[str]:
        target = project or self.project
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--project-root", str(target)],
            text=True,
            capture_output=True,
            check=False,
        )

    def approve(self, project: Path | None = None) -> subprocess.CompletedProcess[str]:
        target = project or self.project
        return self.cli(
            "approve",
            "--definition",
            str(target / ".herdr" / "bots" / "test-bot" / "definition.json"),
            "--approval-id",
            "approval-1",
            "--approver-ref",
            "local-user",
            "--source-ref",
            "security-tests",
            project=target,
        )

    def run_once(self, project: Path | None = None) -> subprocess.CompletedProcess[str]:
        return self.cli("run", "--bot-id", "test-bot", "--once", project=project)

    def write_policy(self, **changes: object) -> None:
        policy = json.loads(self.policy_path.read_text(encoding="utf-8"))
        policy.update(changes)
        self.policy_path.write_text(json.dumps(policy), encoding="utf-8")

    def test_state_directory_symlink_cannot_escape_project(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        state.symlink_to(outside, target_is_directory=True)
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((outside / "state.json").exists())
        self.assertFalse((outside / "events.jsonl").exists())

    def test_stopped_bot_requires_explicit_resume(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        stopped = self.cli("stop", "--bot-id", "test-bot", "--reason", "maintenance")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        blocked = self.run_once()
        self.assertNotEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        resumed = self.cli("resume", "--bot-id", "test-bot")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.run_once().returncode, 0)

    def test_invalid_policy_autonomy_fails_closed(self) -> None:
        self.write_policy(max_autonomy="L9")
        result = self.cli("validate", "--definition", str(self.definition))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_zero_max_bots_rejects_approval(self) -> None:
        self.write_policy(max_bots=0)
        result = self.approve()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_zero_max_active_bots_rejects_run(self) -> None:
        self.write_policy(max_active_bots=0)
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_snapshot_metadata_is_required_and_type_checked(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        snapshot = json.loads(self.approved.read_text(encoding="utf-8"))
        for bad in ({}, "not-an-object"):
            snapshot["approval"] = bad
            self.approved.write_text(json.dumps(snapshot), encoding="utf-8")
            result = self.run_once()
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_approval_cannot_be_replayed_in_another_project(self) -> None:
        other = Path(self._tmp.name) / "other-project"
        shutil.copytree(FIXTURE, other)
        self.assertEqual(self.approve().returncode, 0)
        target = other / ".herdr" / "bots" / "approved" / "test-bot"
        target.mkdir(parents=True)
        shutil.copy2(self.approved, target / "1.json")
        result = self.run_once(project=other)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)

    def test_concurrent_approve_has_exactly_one_winner(self) -> None:
        results: list[subprocess.CompletedProcess[str]] = []
        lock = threading.Lock()

        def worker() -> None:
            result = self.approve()
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(result.returncode for result in results), [0, 2])

    def test_blank_event_id_fails_without_advancing_cursor(self) -> None:
        events_path = self.project / ".herdr" / "bots" / "test-bot" / "events.json"
        payload = json.loads(events_path.read_text(encoding="utf-8"))
        payload["events"][0]["event_id"] = "  "
        events_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        state_path = self.project / ".herdr" / "bots" / "test-bot" / "state" / "state.json"
        self.assertIsNone(json.loads(state_path.read_text(encoding="utf-8"))["cursor"])

    def test_state_write_failure_is_a_stable_runtime_error(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "state.json").mkdir()
        result = self.run_once()
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_object_events_and_health_do_not_traceback(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "events.jsonl").write_text("[]\n", encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_fake_fixture_must_stay_inside_workspace_or_read_scope(self) -> None:
        outside = self.project / ".herdr" / "outside-bot-scope.json"
        source = self.project / ".herdr" / "bots" / "test-bot" / "events.json"
        shutil.copy2(source, outside)
        config_path = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config_path.write_text(
            json.dumps({"scenario": "ok", "fixture_path": ".herdr/outside-bot-scope.json"}),
            encoding="utf-8",
        )
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)

    def test_approval_directory_symlink_cannot_escape_project(self) -> None:
        approved_root = self.project / ".herdr" / "bots" / "approved"
        outside = Path(self._tmp.name) / "outside-approved"
        outside.mkdir()
        approved_root.symlink_to(outside, target_is_directory=True)
        result = self.approve()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((outside / "test-bot" / "1.json").exists())

    def test_interval_trigger_enforces_minimum_interval(self) -> None:
        definition = json.loads(self.definition.read_text(encoding="utf-8"))
        definition["triggers"] = [{"type": "interval", "interval_seconds": 60}]
        self.definition.write_text(json.dumps(definition), encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        second = self.run_once()
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)

    def test_non_object_health_does_not_traceback(self) -> None:
        config_path = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config_path.write_text(json.dumps({"scenario": "auth_expired"}), encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "health.json").write_text("[]\n", encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_policy_symlink_is_rejected_without_traceback(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        policy = self.project / ".herdr" / "bot-policy.json"
        outside = Path(self._tmp.name) / "outside-policy.json"
        outside.write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
        policy.unlink()
        policy.symlink_to(outside)
        result = self.run_once()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_retired_bot_cannot_be_resumed(self) -> None:
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "state.json").write_text(json.dumps({"state": "retired"}), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "resume", "--bot-id", "test-bot", "--project-root", str(self.project)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_quota_slots_are_exclusive_across_concurrent_runs(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        from bot_runtime import StateBlockedError, _quota_slot

        with _quota_slot(self.project.resolve(), 1):
            with self.assertRaises(StateBlockedError):
                with _quota_slot(self.project.resolve(), 1):
                    self.fail("second quota slot must not be acquired")

    def test_valid_read_scope_allows_fixture_outside_workspace(self) -> None:
        shared = self.project / ".herdr" / "shared"
        shared.mkdir(parents=True)
        (shared / "events.json").write_text(
            json.dumps({"next_cursor": {"index": 1}, "events": [{"event_id": "shared-1"}]}),
            encoding="utf-8",
        )
        definition = json.loads(self.definition.read_text(encoding="utf-8"))
        definition["workspace"]["read_scope"] = [".herdr/shared"]
        self.definition.write_text(json.dumps(definition), encoding="utf-8")
        config_path = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config_path.write_text(
            json.dumps({"scenario": "ok", "fixture_path": ".herdr/shared/events.json"}),
            encoding="utf-8",
        )
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_corrupt_failure_counter_does_not_traceback(self) -> None:
        config_path = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config_path.write_text(json.dumps({"scenario": "auth_expired"}), encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "health.json").write_text(
            json.dumps({"consecutive_failures": "bad"}),
            encoding="utf-8",
        )
        result = self.run_once()
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_naive_last_run_timestamp_does_not_break_interval_check(self) -> None:
        definition = json.loads(self.definition.read_text(encoding="utf-8"))
        definition["triggers"] = [{"type": "interval", "interval_seconds": 60}]
        self.definition.write_text(json.dumps(definition), encoding="utf-8")
        self.assertEqual(self.approve().returncode, 0)
        state = self.project / ".herdr" / "bots" / "test-bot" / "state"
        state.mkdir()
        (state / "state.json").write_text(
            json.dumps({"state": "running", "last_run_at": "2026-11-25T00:00:00"}),
            encoding="utf-8",
        )
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_private_state_files_are_not_world_readable(self) -> None:
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        for path in (
            self.approved,
            self.project / ".herdr" / "bots" / "test-bot" / "state" / "state.json",
            self.project / ".herdr" / "bots" / "test-bot" / "state" / "events.jsonl",
        ):
            self.assertEqual(os.stat(path).st_mode & 0o077, 0, oct(os.stat(path).st_mode))


if __name__ == "__main__":
    unittest.main()
