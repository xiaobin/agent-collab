"""Offline tests for scripts/bot_onboarding.py.

A stub meegle binary stands in for the real CLI, so the whole plan/write flow
runs without network, credentials or Herdr.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot_onboarding.py"

STUB = """#!/usr/bin/env python3
import json, os, sys
args = [a for a in sys.argv[1:] if a not in ("--format", "json")]
if os.environ.get("STUB_AUTH") == "0":
    print(json.dumps({"authenticated": False, "reason": "not_logged_in"}))
    raise SystemExit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"authenticated": True, "reason": None}))
elif args[:2] == ["user", "me"]:
    print(json.dumps({"user_key": "user-1", "name": "Alice"}))
elif args[:2] == ["mywork", "todo"]:
    keys = os.environ.get("STUB_PROJECTS", "demo").split(",")
    print(json.dumps({"items": [{"project_key": k, "work_item_id": "1"} for k in keys]}))
else:
    print("unsupported " + " ".join(args), file=sys.stderr)
    raise SystemExit(2)
"""


class OnboardingScriptTest(unittest.TestCase):
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

    def register_owner(self, value="owner:alice"):
        (self.project / ".herdr").mkdir(exist_ok=True)
        (self.project / ".herdr" / "bot-owner.json").write_text(
            json.dumps({"owner_ref": value}), encoding="utf-8"
        )

    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.project,
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def plan(self, *extra, env=None, run_mode="once"):
        mode = () if "--run-mode" in extra else ("--run-mode", run_mode)
        return self.run_cli(
            "plan", "--project-root", ".", "--goal-ref", "goal:test", *mode, *extra, env=env
        )

    def test_missing_owner_stops_and_asks_a_human(self):
        result = self.plan()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("bot-owner.json", result.stderr)
        self.assertIn("Never fall back to git email", result.stderr)
        self.assertFalse((self.project / ".herdr" / "bot-policy.json").exists())

    def test_empty_owner_also_stops(self):
        self.register_owner("  ")
        result = self.plan()
        self.assertEqual(result.returncode, 3)

    def test_logged_out_meegle_stops_for_interactive_login(self):
        self.register_owner()
        env = dict(self.env, STUB_AUTH="0")
        result = self.plan(env=env)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("log in interactively", result.stderr)

    def test_plan_renders_hashes_without_writing_definitions(self):
        self.register_owner()
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["owner_ref"], "owner:alice")
        self.assertEqual(plan["project_key"], "demo")
        self.assertEqual(plan["approve"]["approver_ref"], "owner:alice")
        self.assertEqual(len(plan["files"]), 3)
        for item in plan["files"]:
            self.assertNotIn("REPLACE-WITH-", item["content"])
            self.assertEqual(hashlib.sha256(item["content"].encode()).hexdigest(), item["sha256"])
        self.assertFalse((self.project / ".herdr" / "bots").exists())
        definition = json.loads(plan["files"][1]["content"])
        self.assertEqual(definition["goal_ref"], "goal:test")
        self.assertEqual(definition["identity"]["owner_ref"], "owner:alice")
        self.assertEqual(definition["adapter"]["name"], "feishu")

    def test_ambiguous_project_space_lists_candidates(self):
        self.register_owner()
        env = dict(self.env, STUB_PROJECTS="alpha,beta")
        result = self.plan(env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("alpha", result.stderr)
        self.assertIn("beta", result.stderr)
        chosen = self.plan("--project-key", "beta", env=env)
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        self.assertEqual(json.loads(chosen.stdout)["project_key"], "beta")

    def test_empty_todo_list_asks_for_project_key(self):
        self.register_owner()
        env = dict(self.env, STUB_PROJECTS="")
        result = self.plan(env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no project space found", result.stderr)
        self.assertIn("--project-key", result.stderr)
        self.assertNotIn("ambiguous", result.stderr)
        chosen = self.plan("--project-key", "6ab48e40e56d9a132bd6e4cf", env=env)
        self.assertEqual(chosen.returncode, 0, chosen.stderr)

    def test_write_is_idempotent_and_refuses_drift(self):
        self.register_owner()
        plan_path = self.project / ".herdr" / "plan.json"
        result = self.plan("--output", str(plan_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(plan_path.is_file())
        self.assertFalse((self.project / ".herdr" / "bot-policy.json").exists())

        first = self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(json.loads(first.stdout)["written"]), 3)
        second = self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(json.loads(second.stdout)["unchanged"]), 3)

        definition = self.project / ".herdr" / "bots" / "feishu-sync" / "definition.json"
        definition.write_text(definition.read_text().replace('"revision": 1', '"revision": 2'))
        third = self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(third.returncode, 4)
        self.assertIn("refusing to overwrite", third.stderr)

    def test_interval_minutes_convert_to_seconds(self):
        self.register_owner()
        result = self.plan("--interval-minutes", "30")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["interval_minutes"], 30)
        self.assertEqual(plan["interval_seconds"], 1800)
        definition = json.loads(plan["files"][1]["content"])
        interval = [t for t in definition["triggers"] if t["type"] == "interval"][0]
        self.assertEqual(interval["interval_seconds"], 1800)
        self.assertEqual(definition["triage"]["batch_interval_seconds"], 1800)

    def test_default_interval_is_one_day(self):
        self.register_owner()
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["interval_minutes"], 1440)
        self.assertEqual(plan["interval_seconds"], 86400)

    def test_invalid_interval_is_rejected(self):
        self.register_owner()
        for bad in ("0", "-5", "abc", "43201"):
            with self.subTest(bad=bad):
                result = self.plan("--interval-minutes", bad)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((self.project / ".herdr" / "bot-policy.json").exists())

    def test_run_mode_is_required(self):
        self.register_owner()
        result = self.run_cli(
            "plan", "--project-root", ".", "--goal-ref", "goal:test", "--interval-minutes", "1440"
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("run mode is required", result.stderr)

    def test_run_mode_selects_the_start_command(self):
        self.register_owner()
        once = json.loads(self.plan(run_mode="once").stdout)["run"]
        self.assertEqual(once["mode"], "once")
        self.assertEqual(once["command"][-1], "--once")
        self.assertNotIn("--resident", once["command"])
        self.assertFalse(once["confirm_before_start"])
        self.assertFalse(once["background"])
        self.assertIsNone(once["pid_file"])

        resident = json.loads(
            self.plan("--interval-minutes", "30", run_mode="resident").stdout
        )["run"]
        self.assertEqual(resident["mode"], "resident")
        self.assertIn("--resident", resident["command"])
        self.assertEqual(resident["command"][-1], "1800")
        self.assertTrue(resident["confirm_before_start"])
        self.assertTrue(resident["background"])
        self.assertIn("常驻", resident["confirm_prompt"])
        self.assertEqual(resident["pid_file"], ".herdr/bots/feishu-sync/state/resident.pid")
        self.assertEqual(resident["log_file"], ".herdr/bots/feishu-sync/logs/resident.log")
        self.assertIn("stop", resident["stop_command"])
        self.assertIn("status", resident["status_command"])

    def test_replan_bumps_revision_and_write_accepts_it(self):
        self.register_owner()
        plan_path = self.project / ".herdr" / "plan-1.json"
        self.assertEqual(self.plan("--output", str(plan_path)).returncode, 0)
        self.assertEqual(self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path)).returncode, 0)
        definition = self.project / ".herdr" / "bots" / "feishu-sync" / "definition.json"
        self.assertEqual(json.loads(definition.read_text())["revision"], 1)

        plan_path_2 = self.project / ".herdr" / "plan-2.json"
        replanned = self.plan("--interval-minutes", "60", "--output", str(plan_path_2))
        self.assertEqual(replanned.returncode, 0, replanned.stderr)
        second = json.loads(replanned.stdout)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["previous_revision"], 1)
        self.assertTrue(any("批准已失效" in line for line in second["summary"]))
        written = self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path_2))
        self.assertEqual(written.returncode, 0, written.stderr)
        updated = json.loads(definition.read_text())
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["run_mode"], "once")
        self.assertEqual(updated["triage"]["batch_interval_seconds"], 3600)
        validated = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bot.py"), "validate",
             "--definition", str(definition), "--project-root", "."],
            cwd=self.project, env=self.env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_tampered_plan_is_rejected(self):
        self.register_owner()
        plan_path = self.project / ".herdr" / "plan.json"
        self.assertEqual(self.plan("--output", str(plan_path)).returncode, 0)
        plan = json.loads(plan_path.read_text())
        plan["files"][0]["content"] = plan["files"][0]["content"].replace('"max_bots": 3', '"max_bots": 99')
        plan_path.write_text(json.dumps(plan))
        result = self.run_cli("write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(result.returncode, 4)
        self.assertIn("digest mismatch", result.stderr)
        self.assertFalse((self.project / ".herdr" / "bot-policy.json").exists())


if __name__ == "__main__":
    unittest.main()