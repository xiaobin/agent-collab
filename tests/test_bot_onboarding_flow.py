"""Offline end-to-end acceptance for the bot-onboarding flow.

Covers: plan -> write -> validate -> explain -> approval gate -> approve ->
status, drift invalidating an approval (exit 3), and a fake-adapter
run/status/triage pass. No network, no real meegle, no Herdr.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_bot_onboarding import STUB

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "scripts" / "bot.py"
ONBOARDING = ROOT / "scripts" / "bot_onboarding.py"
FAKE_FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


class OnboardingFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        self.stub = Path(self._tmp.name) / "meegle-stub"
        self.stub.write_text(STUB, encoding="utf-8")
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MEEGLE_")}
        self.env["MEEGLE_BIN"] = str(self.stub)
        (self.project / ".herdr").mkdir()
        (self.project / ".herdr" / "bot-owner.json").write_text(
            json.dumps({"owner_ref": "owner:alice"}), encoding="utf-8"
        )
        self.definition = self.project / ".herdr" / "bots" / "feishu-sync" / "definition.json"
        self.adapter_config = self.project / ".herdr" / "bots" / "feishu-sync" / "adapter-config.json"

    def cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=self.project,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def bot(self, *args):
        return self.cli(BOT, *args)

    def onboard(self):
        plan_path = self.project / ".herdr" / "bots" / "feishu-sync" / "onboarding-plan.json"
        planned = self.cli(
            ONBOARDING, "plan", "--project-root", ".", "--goal-ref", "goal:flow",
            "--output", str(plan_path),
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        written = self.cli(ONBOARDING, "write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(written.returncode, 0, written.stderr)
        return planned

    def approve(self):
        return self.bot(
            "approve", "--definition", str(self.definition), "--project-root", ".",
            "--approval-id", "apr-flow-1",
            "--approver-ref", "owner:alice", "--source-ref", "user-message:approved",
        )

    def test_full_flow_up_to_approved_status(self):
        planned = self.onboard()
        summary = "\n".join(json.loads(planned.stdout)["summary"])
        self.assertIn("owner:alice", summary)
        self.assertIn("demo", summary)

        validated = self.bot("validate", "--definition", str(self.definition), "--project-root", ".")
        self.assertEqual(validated.returncode, 0, validated.stderr)
        explained = self.bot("explain", "--definition", str(self.definition), "--project-root", ".")
        self.assertEqual(explained.returncode, 0, explained.stderr)
        self.assertIn("feishu", explained.stdout)

        blocked = self.bot("run", "--once", "--project-root", ".", "--bot-id", "feishu-sync")
        self.assertEqual(blocked.returncode, 3, blocked.stdout + blocked.stderr)

        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stderr)
        status = self.bot("status", "--project-root", ".", "--bot-id", "feishu-sync")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["state"], "uninitialized")

    def test_definition_drift_invalidates_approval(self):
        self.onboard()
        self.assertEqual(self.approve().returncode, 0)
        self.definition.write_text(
            self.definition.read_text().replace('"revision": 1', '"revision": 2'), encoding="utf-8"
        )
        result = self.bot("run", "--once", "--project-root", ".", "--bot-id", "feishu-sync")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("approval", (result.stdout + result.stderr).lower())

    def test_adapter_config_drift_invalidates_approval(self):
        self.onboard()
        self.assertEqual(self.approve().returncode, 0)
        self.adapter_config.write_text(
            self.adapter_config.read_text().replace('"demo"', '"other-project"'), encoding="utf-8"
        )
        result = self.bot("run", "--once", "--project-root", ".", "--bot-id", "feishu-sync")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)


class FakeAdapterFlowTest(unittest.TestCase):
    """run/status/triage smoke pass on the shipped fake fixture project."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "project"
        shutil.copytree(FAKE_FIXTURE, self.project)

    def bot(self, *args):
        return subprocess.run(
            [sys.executable, str(BOT), *args],
            cwd=self.project, capture_output=True, text=True, check=False,
        )

    def test_run_status_and_triage_report(self):
        definition = self.project / ".herdr" / "bots" / "test-bot" / "definition.json"
        approved = self.bot(
            "approve", "--definition", str(definition), "--project-root", ".",
            "--approval-id", "apr-fake-1",
            "--approver-ref", "owner:alice", "--source-ref", "user-message:approved",
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        ran = self.bot("run", "--once", "--project-root", ".", "--bot-id", "test-bot")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        status = self.bot("status", "--project-root", ".", "--bot-id", "test-bot")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["state"], "ok")

        prepared = self.bot("triage", "prepare", "--project-root", ".", "--bot-id", "test-bot", "--force")
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn("TRIAGE_READY", prepared.stdout)
        batch_id = prepared.stdout.split("batch_id=", 1)[1].split()[0]
        batch = json.loads(
            (self.project / ".herdr" / "bots" / "test-bot" / "state" / "triage" / "batches" / batch_id / "batch.json").read_text()
        )
        claimed = self.bot(
            "triage", "claim", "--project-root", ".", "--bot-id", "test-bot",
            "--batch-id", batch_id, "--attempt-id", "att-flow-1",
        )
        self.assertEqual(claimed.returncode, 0, claimed.stdout + claimed.stderr)
        results = [
            {
                "event_id": event["event_id"],
                "batch_id": batch_id,
                "triage_id": f"tr-{event['event_id']}",
                "triage_policy_version": batch["triage_policy_version"],
                "source": "agent",
                "attempt_id": "att-flow-1",
                "classification": "handoff_or_notice",
                "urgency": "low",
                "status": "proposed",
                "rationale": "offline acceptance result",
                "recommended_action": "no action; read-only notice",
                "required_authorization": "none",
                "evidence_refs": [f"event:{event['event_id']}"],
                "uncertainties": [],
            }
            for event in batch["events"]
        ]
        results_file = self.project / ".herdr" / "bots" / "test-bot" / "state" / "triage" / "flow-results.json"
        results_file.write_text(json.dumps(results), encoding="utf-8")
        applied = self.bot(
            "triage", "apply", "--project-root", ".", "--bot-id", "test-bot",
            "--batch-id", batch_id, "--file", str(results_file),
        )
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)

        report = self.bot("triage", "report", "--project-root", ".", "--bot-id", "test-bot")
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        reports = list((self.project / ".herdr" / "bots" / "test-bot" / "reports" / "triage").glob("*"))
        self.assertTrue(reports, "triage report should produce at least one artifact")


if __name__ == "__main__":
    unittest.main()