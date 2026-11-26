"""Read-only report tests: local rendering, empty states, sync gate.

Everything runs against local fixture files and a stub meegle binary; no test
touches the network or the real meegle CLI.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "scripts" / "bot_report.py"
BOT = ROOT / "scripts" / "bot.py"
ONBOARDING = ROOT / "scripts" / "bot_onboarding.py"
BOT_ID = "feishu-sync"

# Meegle stub that also serves workitem detail and comments, and records every
# invocation so tests can prove the report path stays local.
STUB_FULL = """#!/usr/bin/env python3
import json, os, sys
args = [a for a in sys.argv[1:] if a not in ("--format", "json")]
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\\n")
if args[:2] == ["auth", "status"]:
    print(json.dumps({"authenticated": True, "reason": None}))
elif args[:2] == ["user", "me"]:
    print(json.dumps({"user_key": "user-1", "name": "Alice"}))
elif args[:2] == ["mywork", "todo"]:
    row = {"project_key": "demo", "work_item_info": {"work_item_id": "1", "work_item_name": "能力评估"}}
    print(json.dumps({"items": [row], "list": [row], "total": 1}))
elif args[:2] == ["workitem", "get"]:
    print(json.dumps({"work_item_attribute": {
        "work_item_id": "1", "work_item_name": "能力评估",
        "work_item_status": {"key": "s1", "name": "开发中"},
        "owned_project": {"key": "demo", "name": "Demo Space"},
    }, "work_item_current_node": [
        {"id": "n1", "name": "后端开发", "owners": [{"key": "user-2", "name": "Bob"}]}
    ], "work_item_fields": []}))
elif args[:2] == ["comment", "list"]:
    print(json.dumps({"comments": [
        {"comment_id": "c1", "creator": "user-3", "content": "ok", "created_at": "2026-11-01 10:00:00"},
    ]}))
elif args[:2] == ["user", "search"]:
    print(json.dumps([]))
else:
    print("unsupported " + " ".join(args), file=sys.stderr)
    raise SystemExit(2)
"""


def work_item_event(item_id: str, *, title: str, node: str, owners: list[dict], status: str = "开发中") -> dict:
    return {
        "schema_version": 1,
        "event_id": f"workitem:demo:{item_id}:hash",
        "kind": "work_item.discovered",
        "payload": {
            "project_key": "demo",
            "work_item_id": item_id,
            "snapshot": {
                "todo": {"project_key": "demo", "project_name": "Demo Space",
                         "work_item_info": {"work_item_id": item_id, "work_item_name": title}},
                "detail": {
                    "work_item_attribute": {
                        "work_item_id": item_id, "work_item_name": title,
                        "work_item_status": {"name": status},
                        "owned_project": {"key": "demo", "name": "Demo Space"},
                    },
                    "work_item_current_node": [{"id": "n1", "name": node, "owners": owners}],
                },
            },
        },
    }


def comment_event(item_id: str, comment_id: str, *, creator_name=None, status="resolved") -> dict:
    return {
        "schema_version": 1,
        "event_id": f"comment:demo:{item_id}:{comment_id}",
        "kind": "comment.created",
        "payload": {
            "project_key": "demo",
            "work_item_id": item_id,
            "comment_id": comment_id,
            "creator_user_key": "user-x",
            "creator_name": creator_name,
            "creator_name_status": status,
            "content": "hello",
        },
    }


class ReportRenderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        self.state_dir = self.project / ".herdr" / "bots" / BOT_ID / "state"
        self.state_dir.mkdir(parents=True)

    def write_state(self, state: dict | None):
        if state is not None:
            (self.state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def write_events(self, events: list[dict]):
        text = "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        (self.state_dir / "events.jsonl").write_text(text + ("\n" if text else ""), encoding="utf-8")

    def run_report(self, *extra):
        return subprocess.run(
            [sys.executable, str(REPORT), "report", "--project-root", str(self.project), "--bot-id", BOT_ID, *extra],
            capture_output=True, text=True, check=False,
        )

    def test_summary_renders_space_items_comments_and_node_owners(self):
        self.write_state({"state": "ok", "last_success_at": "2026-11-26T08:00:00Z"})
        self.write_events([
            work_item_event("1", title="能力评估", node="后端开发", owners=[{"key": "u1", "name": "肖斌"}]),
            comment_event("1", "c1"),
            comment_event("1", "c2"),
        ])
        result = self.run_report("--no-write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("空间：Demo Space", result.stdout)
        self.assertIn("未完成待办：1 个 · 评论：2 条", result.stdout)
        self.assertIn("1 · 能力评估", result.stdout)
        self.assertIn("节点：后端开发", result.stdout)
        self.assertIn("节点负责人：肖斌", result.stdout)
        self.assertIn("评论 2 条", result.stdout)
        self.assertIn("最近成功采集：2026-11-26T08:00:00Z", result.stdout)

    def test_limit_hides_extra_items_and_points_to_the_report(self):
        self.write_state({"state": "ok"})
        self.write_events([
            work_item_event(str(index), title=f"待办{index}", node="开发", owners=[{"key": "u1", "name": "A"}])
            for index in range(1, 13)
        ])
        result = self.run_report("--no-write", "--limit", "10")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(1 for line in result.stdout.splitlines() if line[:1].isdigit()), 10)
        self.assertIn("其余 2 条见报告文件", result.stdout)

    def test_uninitialized_state_is_explicit(self):
        result = self.run_report("--no-write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("还没有初始化", result.stdout)
        self.assertNotIn("未完成待办", result.stdout)

    def test_empty_events_report_no_data_instead_of_inventing_items(self):
        self.write_state({"state": "ok"})
        self.write_events([])
        result = self.run_report("--no-write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("还没有采集到任何数据", result.stdout)
        self.assertNotIn("未完成待办", result.stdout)

    def test_approval_invalidated_and_expired_auth_are_explicit(self):
        self.write_state({"state": "approval_invalidated"})
        self.write_events([])
        invalid = self.run_report("--no-write")
        self.assertIn("批准已失效", invalid.stdout)

        self.write_state({"state": "paused", "last_error": "authentication expired: re-authenticate"})
        self.write_events([work_item_event("1", title="旧数据", node="开发", owners=[])])
        expired = self.run_report("--no-write")
        self.assertIn("飞书登录已失效", expired.stdout)
        self.assertIn("旧数据", expired.stdout)

    def test_unknown_owner_and_author_are_marked_not_invented(self):
        self.write_state({"state": "ok"})
        self.write_events([
            work_item_event("1", title="无负责人", node="开发", owners=[{"key": "u9"}]),
            comment_event("1", "c1", creator_name=None, status="unknown"),
        ])
        result = self.run_report("--no-write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未知（u9）", result.stdout)
        self.assertIn("评论 1 条", result.stdout)

    def test_report_file_is_written_with_a_timestamp_and_no_network_call(self):
        self.write_state({"state": "ok"})
        self.write_events([work_item_event("1", title="能力评估", node="开发", owners=[{"key": "u1", "name": "A"}])])
        log = Path(self._tmp.name) / "meegle.log"
        stub = Path(self._tmp.name) / "meegle-stub"
        stub.write_text(STUB_FULL, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEEGLE_")}
        env["MEEGLE_BIN"] = str(stub)
        env["STUB_LOG"] = str(log)
        result = subprocess.run(
            [sys.executable, str(REPORT), "report", "--project-root", str(self.project), "--bot-id", BOT_ID],
            capture_output=True, text=True, check=False, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        reports = sorted((self.project / ".herdr" / "bots" / BOT_ID / "reports").glob("summary-*.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn("能力评估", reports[0].read_text(encoding="utf-8"))
        self.assertFalse(log.exists(), "a read-only report must not call meegle")


class ReportSyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        self.log = Path(self._tmp.name) / "meegle.log"
        self.stub = Path(self._tmp.name) / "meegle-stub"
        self.stub.write_text(STUB_FULL, encoding="utf-8")
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("MEEGLE_")}
        self.env["MEEGLE_BIN"] = str(self.stub)
        self.env["STUB_LOG"] = str(self.log)
        (self.project / ".herdr").mkdir()
        (self.project / ".herdr" / "bot-owner.json").write_text(
            json.dumps({"owner_ref": "owner:alice"}), encoding="utf-8"
        )

    def cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), *args], cwd=self.project, env=self.env,
            capture_output=True, text=True, check=False,
        )

    def onboard(self):
        plan_path = self.project / ".herdr" / "bots" / BOT_ID / "onboarding-plan.json"
        planned = self.cli(
            ONBOARDING, "plan", "--project-root", ".", "--goal-ref", "goal:report",
            "--interval-minutes", "60", "--run-mode", "once", "--output", str(plan_path),
        )
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        written = self.cli(ONBOARDING, "write", "--project-root", ".", "--plan-file", str(plan_path))
        self.assertEqual(written.returncode, 0, written.stdout + written.stderr)

    def approve(self, approval_id="apr-report-1"):
        definition = self.project / ".herdr" / "bots" / BOT_ID / "definition.json"
        approved = self.cli(
            BOT, "approve", "--definition", str(definition), "--project-root", ".",
            "--approval-id", approval_id,
            "--approver-ref", "owner:alice", "--source-ref", "user-message:approved",
        )
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_sync_without_approval_exits_three_and_never_calls_meegle(self):
        self.onboard()
        self.log.unlink(missing_ok=True)
        result = self.cli(REPORT, "report", "--project-root", ".", "--bot-id", BOT_ID, "--sync")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("批准", result.stderr)
        self.assertFalse(self.log.exists(), "the approval gate must fire before any meegle call")
        self.assertFalse((self.project / ".herdr" / "bots" / BOT_ID / "state" / "events.jsonl").exists())

    def test_sync_runs_one_polling_pass_and_then_renders_local_state(self):
        self.onboard()
        self.approve()
        self.log.unlink(missing_ok=True)
        result = self.cli(REPORT, "report", "--project-root", ".", "--bot-id", BOT_ID, "--sync")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Demo Space", result.stdout)
        self.assertIn("能力评估", result.stdout)
        self.assertIn("节点负责人：Bob", result.stdout)
        # One read-only pass: auth status, user me, mywork todo, workitem get, comment list (and user search for names).
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls)
        for call in calls:
            self.assertFalse(any(word in call for word in ("create", "update", "delete", "comment add")), call)

    def test_sync_twice_reports_the_interval_block_without_blaming_approval(self):
        self.onboard()
        self.approve()
        first = self.cli(REPORT, "report", "--project-root", ".", "--bot-id", BOT_ID, "--sync", "--no-write")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self.cli(REPORT, "report", "--project-root", ".", "--bot-id", BOT_ID, "--sync", "--no-write")
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        self.assertIn("minimum interval", second.stderr)
        self.assertNotIn("批准门禁", second.stderr)

    def test_run_once_prints_the_summary_for_feishu_bots(self):
        self.onboard()
        self.approve()
        ran = self.cli(BOT, "run", "--once", "--project-root", ".", "--bot-id", BOT_ID)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        combined = ran.stdout + ran.stderr
        self.assertIn("飞书待办摘要", combined)
        self.assertIn("能力评估", combined)
        self.assertIn("评论：1 条", combined)
        # stdout stays a single JSON document for scripts.
        self.assertEqual(json.loads(ran.stdout)["status"], "ok")



class ReportSkillContractTest(unittest.TestCase):
    """The bot-report skill must be a single user entry and stay read-only by default."""

    def test_skill_is_registered_for_all_three_hosts(self):
        skill = ROOT / "skills" / "bot-report" / "SKILL.md"
        self.assertTrue(skill.is_file(), skill)
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn("./skills/bot-report", package["pi"]["skills"])
        text = skill.read_text(encoding="utf-8")
        for command in (
            "/agent-collab:bot-report",
            "/skill:bot-report",
            "$agent-collab:bot-report",
        ):
            self.assertIn(command, text)

    def test_skill_defaults_to_local_and_gates_sync(self):
        text = (ROOT / "skills" / "bot-report" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--sync", text)
        self.assertIn("退出码 3", text)
        self.assertIn("不自动重批", text)
        self.assertIn("不评论、不改状态、不建单", text)
        self.assertIn("bot.py run --once", text)

    def test_readme_documents_later_views(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("接入后随时查看", readme)
        for command in ("/agent-collab:bot-report", "/skill:bot-report", "$agent-collab:bot-report"):
            self.assertIn(command, readme)


if __name__ == "__main__":
    unittest.main()