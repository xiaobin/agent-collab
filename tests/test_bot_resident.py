import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"
sys.path.insert(0, str(ROOT / "scripts"))

import bot_runtime


class ResidentCliTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.bot_dir = self.project / ".herdr" / "bots" / "test-bot"
        self.definition_path = self.bot_dir / "definition.json"
        self.config_path = self.bot_dir / "adapter-config.json"
        self.process = None

    def tearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate(timeout=5)
        self.tempdir.cleanup()

    def cli(self, *args, timeout=20):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def approve(self, approval_id="apr-resident-1"):
        return self.cli(
            "approve", "--definition", str(self.definition_path),
            "--project-root", str(self.project),
            "--approval-id", approval_id, "--approver-ref", "local-user",
            "--source-ref", "resident-test",
        )

    def resident_args(self, *, interval="0.05", max_rounds=None):
        args = [
            "run", "--project-root", str(self.project), "--bot-id", "test-bot",
            "--resident", "--interval-seconds", str(interval),
        ]
        if max_rounds is not None:
            args.extend(["--max-rounds", str(max_rounds)])
        return args

    def start_resident(self, **kwargs):
        self.process = subprocess.Popen(
            [sys.executable, str(SCRIPT), *self.resident_args(**kwargs)],
            cwd=self.project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return self.process

    def wait_for_file(self, path, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if self.process is not None and self.process.poll() is not None:
                self.fail(f"resident exited early: {self.process.returncode} {self.process.stderr.read()}")
            time.sleep(0.02)
        self.fail(f"timed out waiting for {path}")

    def state(self):
        return json.loads((self.bot_dir / "state" / "state.json").read_text())

    def resident_state(self):
        return json.loads((self.bot_dir / "state" / "resident.json").read_text())

    def run_log(self):
        path = self.bot_dir / "state" / "run.log"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_interval_trigger_is_used_when_flag_absent(self):
        definition = json.loads(self.definition_path.read_text())
        definition["triggers"] = [{"type": "interval", "interval_seconds": 60}]
        self.definition_path.write_text(json.dumps(definition))
        self.assertEqual(self.approve().returncode, 0)
        result = self.cli(
            "run", "--project-root", str(self.project),
            "--bot-id", "test-bot", "--resident", "--max-rounds", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual([row["events_new"] for row in rows if "round" in row], [2])

    def test_resident_repeats_and_persists_cursor(self):
        self.assertEqual(self.approve().returncode, 0)
        result = self.cli(*self.resident_args(max_rounds=3))
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        rounds = [row for row in rows if "round" in row]
        self.assertEqual([row["events_new"] for row in rounds], [2, 0, 0])
        self.assertEqual([row["events_skipped"] for row in rounds], [0, 2, 2])
        self.assertEqual(len({row["cursor_sha256"] for row in rounds}), 1)
        self.assertTrue(all(row["agent_calls"] == 0 for row in rounds))
        self.assertEqual(self.state()["cursor"], {"index": 2})
        resident = self.resident_state()
        self.assertEqual(resident["state"], "stopped")
        self.assertIn("started_at", resident)
        self.assertIn("heartbeat_at", resident)
        self.assertIsNone(resident["lease_expires_at"])
        health = json.loads((self.bot_dir / "state" / "health.json").read_text())
        self.assertEqual(health["state"], "stopped")
        self.assertEqual(health["daemon_pid"], resident["pid"])
        log = self.run_log()
        self.assertEqual(len([row for row in log if row["event"] == "round_start"]), 3)
        self.assertEqual(len([row for row in log if row["event"] == "round_end"]), 3)
        self.assertEqual(len((self.bot_dir / "state" / "events.jsonl").read_text().splitlines()), 2)

    def test_duplicate_start_is_rejected_and_sigterm_is_graceful(self):
        self.assertEqual(self.approve().returncode, 0)
        self.start_resident(interval="2", max_rounds=20)
        self.wait_for_file(self.bot_dir / "state" / "resident.json")
        duplicate = self.cli(*self.resident_args(max_rounds=1))
        self.assertEqual(duplicate.returncode, 4)
        self.assertIn("resident", duplicate.stderr.lower())
        self.process.send_signal(signal.SIGTERM)
        stdout, stderr = self.process.communicate(timeout=15)
        self.assertEqual(self.process.returncode, 0, stderr)
        self.assertEqual(self.state()["state"], "stopped")
        self.assertEqual(self.resident_state()["stop_reason"], "signal:SIGTERM")
        self.assertTrue(any(row["event"] == "signal" for row in self.run_log()))

    def test_external_stop_then_restart_has_zero_replay(self):
        self.assertEqual(self.approve().returncode, 0)
        self.start_resident(interval="5", max_rounds=20)
        events = self.bot_dir / "state" / "events.jsonl"
        self.wait_for_file(events)
        deadline = time.monotonic() + 5
        stopped = None
        while time.monotonic() < deadline:
            stopped = self.cli("stop", "--project-root", str(self.project), "--bot-id", "test-bot", "--reason", "test-stop")
            if stopped.returncode != 4:
                break
            time.sleep(0.05)
        self.assertIsNotNone(stopped)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.process.communicate(timeout=15)
        self.assertEqual(self.process.returncode, 0)
        self.assertEqual(self.state()["state"], "stopped")
        resumed = self.cli("resume", "--project-root", str(self.project), "--bot-id", "test-bot")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        restarted = self.cli(*self.resident_args(max_rounds=1))
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        first = json.loads(restarted.stdout.splitlines()[0])
        self.assertEqual(first["events_new"], 0)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(len(events.read_text().splitlines()), 2)

    def test_auth_failure_keeps_existing_cursor_and_resumes_without_replay(self):
        self.assertEqual(self.approve().returncode, 0)
        seeded = self.cli("run", "--project-root", str(self.project), "--bot-id", "test-bot", "--once")
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        seeded_cursor = self.state()["cursor"]
        self.assertEqual(seeded_cursor, {"index": 2})
        events = self.bot_dir / "state" / "events.jsonl"
        self.assertEqual(len(events.read_text().splitlines()), 2)

        definition = json.loads(self.definition_path.read_text())
        definition["revision"] = 2
        self.definition_path.write_text(json.dumps(definition))
        self.config_path.write_text(json.dumps({"scenario": "auth_expired", "fixture_path": ".herdr/bots/test-bot/events.json"}))
        self.assertEqual(self.approve("apr-resident-2").returncode, 0)
        result = self.cli(*self.resident_args(max_rounds=5))
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("authentication required", result.stderr)
        self.assertEqual(len([line for line in result.stdout.splitlines() if '"round"' in line]), 1)
        self.assertEqual(self.state()["state"], "paused")
        self.assertEqual(self.state()["cursor"], seeded_cursor)
        self.assertEqual(self.resident_state()["state"], "auth_required")
        self.assertEqual(len([row for row in self.run_log() if row["event"] == "round_start"]), 1)
        self.assertTrue(any(row["event"] == "auth_stop" for row in self.run_log()))

        definition["revision"] = 3
        self.definition_path.write_text(json.dumps(definition))
        self.config_path.write_text(json.dumps({"scenario": "ok", "fixture_path": ".herdr/bots/test-bot/events.json"}))
        self.assertEqual(self.approve("apr-resident-3").returncode, 0)
        self.assertEqual(self.cli("resume", "--project-root", str(self.project), "--bot-id", "test-bot").returncode, 0)
        recovered = self.cli(*self.resident_args(max_rounds=1))
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout.splitlines()[0])["events_new"], 0)
        self.assertEqual(len(events.read_text().splitlines()), 2)

    def test_definition_drift_stops_before_the_next_round(self):
        self.assertEqual(self.approve().returncode, 0)
        self.start_resident(interval="1.2", max_rounds=3)
        events_path = self.bot_dir / "state" / "events.jsonl"
        self.wait_for_file(events_path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(events_path.read_text().splitlines()) < 2:
            time.sleep(0.05)
        definition = json.loads(self.definition_path.read_text())
        definition["goal_ref"] = "TEST-GOAL-CHANGED"
        self.definition_path.write_text(json.dumps(definition))
        stdout, stderr = self.process.communicate(timeout=10)
        self.assertEqual(self.process.returncode, 3, stderr)
        self.assertIn("approval invalidated", stderr)
        self.assertEqual(len([line for line in stdout.splitlines() if '"round"' in line]), 1)
        self.assertEqual(self.state()["state"], "approval_invalidated")
        self.assertIn("definition changed", self.state()["last_error"])
        self.assertEqual(self.resident_state()["state"], "approval_invalidated")

    def test_expired_approval_stops_before_the_next_round(self):
        expires = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        approved = self.cli(
            "approve", "--definition", str(self.definition_path),
            "--project-root", str(self.project),
            "--approval-id", "apr-short", "--approver-ref", "local-user",
            "--source-ref", "resident-test", "--expires-at", expires,
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        result = self.cli(*self.resident_args(interval="1.2", max_rounds=3))
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("approval invalidated", result.stderr)
        rounds = [json.loads(line) for line in result.stdout.splitlines() if '"round"' in line]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(self.state()["state"], "approval_invalidated")
        self.assertIn("expired", self.state()["last_error"])
        self.assertEqual(self.resident_state()["state"], "approval_invalidated")

    def test_partial_failure_is_degraded_and_cursor_is_preserved(self):
        self.config_path.write_text(json.dumps({"scenario": "partial_failure", "fixture_path": ".herdr/bots/test-bot/events.json"}))
        self.assertEqual(self.approve().returncode, 0)
        result = self.cli(*self.resident_args(interval="0.01", max_rounds=2))
        self.assertEqual(result.returncode, 5, result.stderr)
        rounds = [json.loads(line) for line in result.stdout.splitlines() if '"round"' in line]
        self.assertEqual([row["status"] for row in rounds], ["degraded", "degraded"])
        self.assertTrue(all(row["events_new"] == 0 for row in rounds))
        self.assertEqual(self.state()["state"], "degraded")
        self.assertIsNone(self.state()["cursor"])
        self.assertEqual(self.resident_state()["state"], "failed")
        health = json.loads((self.bot_dir / "state" / "health.json").read_text())
        self.assertEqual(health["state"], "degraded")
        self.assertEqual(health["consecutive_failures"], 2)
        log = self.run_log()
        self.assertEqual([row.get("error") for row in log if row["event"] == "round_end"], ["partial_failure", "partial_failure"])
        self.assertTrue(any(row["event"] == "daemon_failed" for row in log))

    def test_deleted_lock_file_does_not_start_a_second_resident(self):
        self.assertEqual(self.approve().returncode, 0)
        self.start_resident(interval="5", max_rounds=20)
        resident_path = self.bot_dir / "state" / "resident.json"
        self.wait_for_file(resident_path)
        (self.bot_dir / "state" / "resident.lock").unlink()
        duplicate = self.cli(*self.resident_args(max_rounds=1))
        self.assertEqual(duplicate.returncode, 4, duplicate.stderr)
        self.assertIn("another resident process is active", duplicate.stderr)
        self.assertIsNone(self.process.poll())
        self.process.send_signal(signal.SIGTERM)
        self.process.communicate(timeout=15)
        self.assertEqual(self.process.returncode, 0)

    def test_status_marks_dead_process_and_expired_lease_as_stale(self):
        resident = {
            "schema_version": 1,
            "bot_id": "test-bot",
            "pid": 99999999,
            "state": "running",
            "started_at": "2026-01-01T00:00:00+00:00",
            "heartbeat_at": "2026-01-01T00:00:00+00:00",
            "lease_expires_at": "2026-01-01T00:01:00+00:00",
            "round": 1,
        }
        state_dir = self.bot_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "resident.json").write_text(json.dumps(resident))
        result = self.cli("status", "--project-root", str(self.project), "--bot-id", "test-bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["resident"]["state"], "stale")
        self.assertEqual(payload["resident"]["stale_reason"], "process_not_running")

        resident["pid"] = os.getpid()
        resident["lease_expires_at"] = "2026-01-01T00:01:00+00:00"
        (state_dir / "resident.json").write_text(json.dumps(resident))
        result = self.cli("status", "--project-root", str(self.project), "--bot-id", "test-bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["resident"]["state"], "stale")
        self.assertEqual(payload["resident"]["stale_reason"], "lease_expired")

    def test_repeated_upstream_errors_keep_retrying_until_signal(self):
        self.config_path.write_text(json.dumps({"scenario": "upstream_error", "fixture_path": ".herdr/bots/test-bot/events.json"}))
        self.assertEqual(self.approve().returncode, 0)
        self.start_resident(interval="0.01")
        log_path = self.bot_dir / "state" / "run.log"
        deadline = time.monotonic() + 10
        starts = 0
        while time.monotonic() < deadline:
            if log_path.exists():
                starts = sum(
                    1
                    for line in log_path.read_text().splitlines()
                    if line.strip() and json.loads(line).get("event") == "round_start"
                )
                if starts >= 3:
                    break
            time.sleep(0.05)
        self.assertGreaterEqual(starts, 3)
        self.assertIsNone(self.process.poll())
        self.process.send_signal(signal.SIGTERM)
        self.process.communicate(timeout=15)
        self.assertEqual(self.process.returncode, 0)
        self.assertEqual(self.state()["state"], "stopped")
        log = self.run_log()
        self.assertFalse(any(row["event"] == "auth_stop" for row in log))
        self.assertTrue(any(row["event"] == "backoff" for row in log))
        self.assertEqual(
            [row.get("error") for row in log if row["event"] == "round_end"][:3],
            ["upstream_error", "upstream_error", "upstream_error"],
        )

    def test_run_log_does_not_contain_event_content(self):
        fixture = self.bot_dir / "events.json"
        payload = json.loads(fixture.read_text())
        payload["events"][0]["payload"] = {"content": "SENSITIVE-COMMENT-MARKER"}
        fixture.write_text(json.dumps(payload))
        self.assertEqual(self.approve().returncode, 0)
        result = self.cli(*self.resident_args(max_rounds=1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SENSITIVE-COMMENT-MARKER", (self.bot_dir / "state" / "run.log").read_text())


class ResidentBackoffTest(unittest.TestCase):
    def test_consecutive_failures_back_off_and_then_recover(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            shutil.copytree(FIXTURE, project)
            definition = json.loads((project / ".herdr/bots/test-bot/definition.json").read_text())
            policy = json.loads((project / ".herdr/bot-policy.json").read_text())
            calls = []
            emitted = []

            def once(project_root, definition, policy):
                calls.append(len(calls) + 1)
                if len(calls) < 3:
                    return {"status": "degraded", "events_new": 0, "events_skipped": 0, "cursor": None, "error": "upstream_timeout", "agent_calls": 0}, 5
                return {"status": "ok", "events_new": 0, "events_skipped": 0, "cursor": {"index": 2}, "agent_calls": 0}, 0

            with patch.object(bot_runtime, "RESIDENT_BACKOFF_INITIAL_SECONDS", 0.01), patch.object(bot_runtime, "RESIDENT_BACKOFF_MAX_SECONDS", 0.02):
                code = bot_runtime.run_resident(
                    project, definition, policy,
                    interval_seconds=0.01, max_rounds=3,
                    once=once, emit=emitted.append,
                )
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 3)
            self.assertEqual([row["status"] for row in emitted if "round" in row], ["degraded", "degraded", "ok"])
            log_path = project / ".herdr/bots/test-bot/state/run.log"
            rows = [json.loads(line) for line in log_path.read_text().splitlines()]
            backoffs = [row["sleep_seconds"] for row in rows if row["event"] == "backoff"]
            self.assertEqual(backoffs, [0.01, 0.02])


if __name__ == "__main__":
    unittest.main()
