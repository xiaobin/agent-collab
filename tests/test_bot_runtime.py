import copy
import fcntl
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


class BotRuntimeCliTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.bot_dir = self.project / ".herdr" / "bots" / "test-bot"
        self.definition_path = self.bot_dir / "definition.json"
        self.adapter_config_path = self.bot_dir / "adapter-config.json"
        self.definition = json.loads(
            self.definition_path.read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_definition(self, definition):
        self.definition_path.write_text(
            json.dumps(definition, ensure_ascii=False), encoding="utf-8"
        )

    def write_adapter_config(self, config):
        self.adapter_config_path.write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8"
        )

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )

    def approve(self, approval_id="apr-runtime-1"):
        return self.cli(
            "approve",
            "--definition",
            str(self.definition_path),
            "--project-root",
            str(self.project),
            "--approval-id",
            approval_id,
            "--approver-ref",
            "local-user",
            "--source-ref",
            "message-runtime-1",
        )

    def run_once(self):
        return self.cli(
            "run",
            "--project-root",
            str(self.project),
            "--bot-id",
            "test-bot",
            "--once",
        )

    def state_path(self):
        return self.bot_dir / "state" / "state.json"

    def health_path(self):
        return self.bot_dir / "state" / "health.json"

    def events_path(self):
        return self.bot_dir / "state" / "events.jsonl"

    def lifecycle_path(self):
        return self.project / ".herdr" / "bots" / "lifecycle.jsonl"

    def test_once_run_requires_an_approval(self):
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("approval", result.stderr.lower())
        self.assertFalse(self.events_path().exists())

    def test_once_run_polls_fake_adapter_and_records_events(self):
        approval = self.approve()
        self.assertEqual(approval.returncode, 0, approval.stderr)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["bot_id"], "test-bot")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["events_new"], 2)
        self.assertEqual(payload["events_skipped"], 0)
        self.assertEqual(payload["agent_calls"], 0)
        self.assertEqual(payload["cursor"], {"index": 2})
        lines = self.events_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            [json.loads(line)["event_id"] for line in lines],
            ["evt-1", "evt-2"],
        )
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["cursor"], {"index": 2})
        self.assertEqual(state["state"], "ok")
        health = json.loads(self.health_path().read_text(encoding="utf-8"))
        self.assertEqual(health["state"], "ok")
        lifecycle = [
            json.loads(line)
            for line in self.lifecycle_path().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(lifecycle[-1]["event"], "run_succeeded")
        self.assertEqual(lifecycle[-1]["agent_calls"], 0)

    def test_duplicate_events_within_one_poll_are_written_once(self):
        config = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config.write_text(
            json.dumps(
                {
                    "scenario": "ok",
                    "fixture_path": ".herdr/bots/test-bot/duplicate-events.json",
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["events_new"], 1)
        self.assertEqual(output["events_skipped"], 1)
        lines = self.events_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

    def test_snapshot_with_inconsistent_digest_is_rejected(self):
        self.assertEqual(self.approve().returncode, 0)
        approved = (
            self.project
            / ".herdr"
            / "bots"
            / "approved"
            / "test-bot"
            / "1.json"
        )
        snapshot = json.loads(approved.read_text(encoding="utf-8"))
        snapshot["definition"]["goal"] = "tampered after approval"
        approved.write_text(json.dumps(snapshot), encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("digest", result.stderr.lower())

    def test_second_run_skips_duplicate_event_ids(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["events_new"], 0)
        self.assertEqual(payload["events_skipped"], 2)
        lines = self.events_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_unapproved_revision_is_rejected(self):
        self.assertEqual(self.approve().returncode, 0)
        definition = copy.deepcopy(self.definition)
        definition["revision"] = 2
        self.write_definition(definition)
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("revision", result.stderr)

    def test_snapshot_digest_mismatch_is_rejected(self):
        self.assertEqual(self.approve().returncode, 0)
        approved = (
            self.project
            / ".herdr"
            / "bots"
            / "approved"
            / "test-bot"
            / "1.json"
        )
        snapshot = json.loads(approved.read_text(encoding="utf-8"))
        snapshot["definition"]["goal_ref"] = "TAMPERED"
        approved.write_text(json.dumps(snapshot), encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("digest", result.stderr)
        self.assertFalse(self.events_path().exists())

    def test_adapter_config_drift_is_rejected(self):
        self.assertEqual(self.approve().returncode, 0)
        config = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config.write_text(
            json.dumps(
                {
                    "scenario": "auth_expired",
                    "fixture_path": ".herdr/bots/test-bot/events.json",
                }
            ),
            encoding="utf-8",
        )
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("adapter config changed", result.stderr)

    def test_policy_drift_is_rejected(self):
        self.assertEqual(self.approve().returncode, 0)
        policy_path = self.project / ".herdr" / "bot-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["max_bots"] = 4
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("policy changed", result.stderr)

    def test_auth_failure_is_reported_without_advancing_cursor(self):
        self.write_adapter_config(
            {"scenario": "auth_expired", "fixture_path": ".herdr/bots/test-bot/empty-events.json"}
        )
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 6)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "degraded")
        self.assertIsNone(state["cursor"])
        self.assertEqual(state["last_error"], "authentication_expired")
        if self.events_path().exists():
            self.assertEqual(self.events_path().read_text(encoding="utf-8"), "")

    def test_partial_failure_does_not_advance_cursor(self):
        self.write_adapter_config(
            {"scenario": "partial_failure", "fixture_path": ".herdr/bots/test-bot/events.json"}
        )
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 5)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "degraded")
        self.assertIsNone(state["cursor"])
        self.assertEqual(state["last_error"], "partial_failure")

    def test_expired_snapshot_is_rejected_at_run_time(self):
        self.assertEqual(self.approve().returncode, 0)
        approved = (
            self.project
            / ".herdr"
            / "bots"
            / "approved"
            / "test-bot"
            / "1.json"
        )
        snapshot = json.loads(approved.read_text(encoding="utf-8"))
        snapshot["approval"]["expires_at"] = "2000-01-01T00:00:00+00:00"
        approved.write_text(json.dumps(snapshot), encoding="utf-8")
        result = self.run_once()
        self.assertEqual(result.returncode, 3)
        self.assertIn("expired", result.stderr.lower())
        self.assertFalse(self.events_path().exists())

    def test_lock_conflict_exits_four(self):
        self.assertEqual(self.approve().returncode, 0)
        lock_path = self.bot_dir / "state" / "lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_once()
        self.assertEqual(result.returncode, 4)
        self.assertIn("locked", result.stderr.lower())
        self.assertFalse(self.events_path().exists())

    def test_agent_batch_runtime_is_refused_in_stage_one(self):
        definition = copy.deepcopy(self.definition)
        definition["runtime"]["kind"] = "agent_batch"
        definition["runtime"]["agent"] = {
            "kind": "claude",
            "model_profile_ref": "local-only-profile",
            "session_mode": "ephemeral",
            "timeout_seconds": 60,
            "max_agent_calls_per_day": 1,
            "output_schema_ref": None,
            "permission_profile": "read_only",
        }
        self.write_definition(definition)
        self.assertEqual(self.approve().returncode, 0)
        result = self.run_once()
        self.assertEqual(result.returncode, 2)
        self.assertIn("agent runtime not implemented", result.stderr)
        lifecycle = [
            json.loads(line)
            for line in self.lifecycle_path().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(lifecycle[-1].get("agent_calls", 0), 0)

    def test_resident_mode_is_refused_in_stage_one(self):
        self.assertEqual(self.approve().returncode, 0)
        result = self.cli(
            "run",
            "--project-root",
            str(self.project),
            "--bot-id",
            "test-bot",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("resident", result.stderr)
        self.assertIn("--once", result.stderr)

    def test_status_reports_state_and_cursor(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        result = self.cli(
            "status",
            "--project-root",
            str(self.project),
            "--bot-id",
            "test-bot",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["cursor"], {"index": 2})

    def test_stop_marks_the_bot_stopped(self):
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.run_once().returncode, 0)
        result = self.cli(
            "stop",
            "--project-root",
            str(self.project),
            "--bot-id",
            "test-bot",
            "--reason",
            "stage-one test",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_path().read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "stopped")
        lifecycle = [
            json.loads(line)
            for line in self.lifecycle_path().read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(lifecycle[-1]["event"], "stopped")


if __name__ == "__main__":
    unittest.main()