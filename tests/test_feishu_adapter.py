import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"
sys.path.insert(0, str(ROOT / "scripts"))

from bot_adapters.base import AdapterError
from bot_adapters.feishu_sync import FeishuAdapter

FAKE_MEEGLE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

fixture = json.loads(Path(os.environ["FAKE_MEEGLE_FIXTURE"]).read_text())
log = os.environ.get("FAKE_MEEGLE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
command = " ".join(args[:2])

def arg(name, default=None):
    return args[args.index(name) + 1] if name in args else default

failure = fixture.get("failures", {}).get(command)
if failure:
    sys.stderr.write(failure.get("stderr", "synthetic failure"))
    raise SystemExit(failure.get("returncode", 1))
if command == "auth status":
    print(json.dumps(fixture.get("auth", {"authenticated": True})))
elif command == "user me":
    print(json.dumps(fixture.get("user", {"user_key": "user-current"})))
elif command == "mywork todo":
    print(json.dumps(fixture["todo_pages"].get(arg("--page-num", "1"), {"list": [], "total": 0})))
elif command == "comment list":
    key = f'{arg("--project-key")}:{arg("--work-item-id")}:{arg("--page-num", "1")}'
    print(json.dumps(fixture["comment_pages"].get(key, {"comments": [], "pagination": {"page_num": int(arg("--page-num", "1")), "total_pages": 0}})))
elif command == "workitem get":
    key = f'{arg("--project-key")}:{arg("--work-item-id")}'
    print(json.dumps(fixture["details"][key]))
else:
    sys.stderr.write("write or unknown command forbidden: " + command)
    raise SystemExit(9)
'''


def work_item(project_key="pk-synthetic", item_id=1001, name="Synthetic item"):
    return {
        "project_key": project_key,
        "project_name": "Synthetic space",
        "work_item_info": {
            "work_item_id": item_id,
            "work_item_name": name,
            "work_item_type_key": "story",
        },
        "node_info": {"node_name": "Doing", "node_state_key": "doing"},
        "state_info": {"start_state_name": "Open", "end_state_name": "Closed"},
        "schedule": {"start_time": "2026-01-01", "end_time": "2026-01-02"},
    }


def detail(updated="2026-01-03T00:00:00+08:00"):
    return {
        "work_item_attribute": {
            "work_item_id": 1001,
            "work_item_name": "Synthetic item",
            "work_item_mod": "node_flow",
            "work_item_status": {"key": "doing", "name": "Doing"},
            "work_item_type": {"key": "story", "name": "Story"},
            "owned_project": "pk-synthetic",
            "create_time": "2026-01-01T00:00:00+08:00",
            "update_time": updated,
            "role_members": [{"key": "owner", "members": [{"key": "user-1", "name": "Synthetic owner"}]}],
        },
        "work_item_current_node": [{"id": "doing", "name": "Doing", "owners": [{"key": "user-1"}]}],
        "work_item_fields": [{"key": "description", "name": "Description", "value": "synthetic"}],
    }


def base_fixture():
    return {
        "auth": {"authenticated": True, "expires_in_minutes": 30},
        "user": {"user_key": "user-current", "name_cn": "Synthetic user"},
        "todo_pages": {"1": {"list": [work_item()], "total": 1}, "2": {"list": [], "total": 1}},
        "comment_pages": {
            "pk-synthetic:1001:1": {
                "comments": [
                    {"comment_id": "c2", "content": "second", "created_at": "2026-01-02 00:00:02", "creator": "creator-2", "file_url": ""},
                    {"comment_id": "c1", "content": "@alice first", "created_at": "2026-01-02 00:00:01", "creator": "creator-1", "file_url": "https://example.invalid/synthetic-file"},
                ],
                "pagination": {"page_num": 1, "page_size": 20, "total": 2, "total_pages": 1},
            }
        },
        "details": {"pk-synthetic:1001": detail()},
    }


class FeishuAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.workspace = self.project / ".herdr" / "bots" / "test-bot"
        self.workspace.mkdir(parents=True)
        self.fake = Path(self.tmp.name) / "fake-meegle"
        self.fake.write_text(FAKE_MEEGLE, encoding="utf-8")
        self.fake.chmod(0o755)
        self.fixture_path = Path(self.tmp.name) / "fixture.json"
        self.log_path = Path(self.tmp.name) / "calls.jsonl"
        self.write_fixture(base_fixture())
        self.env = patch.dict(os.environ, {
            "FAKE_MEEGLE_FIXTURE": str(self.fixture_path),
            "FAKE_MEEGLE_LOG": str(self.log_path),
        })
        self.env.start()
        self.config = {"meegle_bin": str(self.fake), "fetch_details": True}

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def write_fixture(self, payload) -> None:
        self.fixture_path.write_text(json.dumps(payload), encoding="utf-8")

    def poll(self, cursor=None, config=None):
        return FeishuAdapter().poll(
            cursor=cursor,
            config=config or self.config,
            project_root=self.project,
            workspace_root=self.workspace,
            read_scope=[],
        )

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def test_first_poll_emits_snapshot_and_sorted_comments(self) -> None:
        result = self.poll()
        kinds = [event["kind"] for event in result.events]
        self.assertEqual(kinds.count("work_item.discovered"), 1)
        comments = [event for event in result.events if event["kind"] == "comment.created"]
        self.assertEqual([event["payload"]["comment_id"] for event in comments], ["c1", "c2"])
        first = comments[0]["payload"]
        self.assertEqual(first["creator_user_key"], "creator-1")
        self.assertIsNone(first["creator_name"])
        self.assertEqual(first["mentions_text"], ["@alice"])
        self.assertEqual(first["attachments"], [{"name": None, "url": "https://example.invalid/synthetic-file"}])
        snapshot = next(event for event in result.events if event["kind"] == "work_item.discovered")
        self.assertNotIn("web_url", snapshot["payload"])
        self.assertEqual(len(result.next_cursor["items"]), 1)
        self.assertTrue(all(call[:2] in (["auth", "status"], ["mywork", "todo"], ["comment", "list"], ["workitem", "get"], ["user", "me"]) for call in self.calls()))

    def test_second_poll_with_same_data_emits_zero_events(self) -> None:
        first = self.poll()
        second = self.poll(cursor=first.next_cursor)
        self.assertEqual(second.events, [])
        self.assertEqual(second.next_cursor, first.next_cursor)

    def test_changed_snapshot_emits_update_without_reemitting_comments(self) -> None:
        first = self.poll()
        changed = base_fixture()
        changed["details"]["pk-synthetic:1001"] = detail("2026-01-04T00:00:00+08:00")
        self.write_fixture(changed)
        second = self.poll(cursor=first.next_cursor)
        self.assertEqual([event["kind"] for event in second.events], ["work_item.updated"])
        self.assertNotEqual(second.next_cursor, first.next_cursor)

    def test_duplicate_todo_rows_are_collapsed(self) -> None:
        payload = base_fixture()
        payload["todo_pages"] = {"1": {"list": [work_item(), work_item(), work_item()], "total": 3}, "2": {"list": [], "total": 3}}
        self.write_fixture(payload)
        result = self.poll()
        self.assertEqual(len([e for e in result.events if e["kind"] == "work_item.discovered"]), 1)
        comment_calls = [call for call in self.calls() if call[:2] == ["comment", "list"]]
        self.assertEqual(len(comment_calls), 1)

    def test_unstable_array_order_does_not_emit_update(self) -> None:
        enriched = base_fixture()
        detail_payload = enriched["details"]["pk-synthetic:1001"]
        detail_payload["work_item_current_node"].append({"id": "review", "name": "Review", "owners": [{"key": "user-2"}]})
        members = detail_payload["work_item_attribute"]["role_members"]
        members.append({"key": "qa", "members": [{"key": "user-2", "name": "QA"}, {"key": "user-1", "name": "Synthetic owner"}]})
        members[0]["members"].append({"key": "user-2", "name": "QA"})
        self.write_fixture(enriched)
        first = self.poll()

        reordered = json.loads(json.dumps(enriched))
        detail_payload = reordered["details"]["pk-synthetic:1001"]
        detail_payload["work_item_current_node"] = list(reversed(detail_payload["work_item_current_node"]))
        members = detail_payload["work_item_attribute"]["role_members"]
        for role in members:
            role["members"] = list(reversed(role["members"]))
        detail_payload["work_item_attribute"]["role_members"] = list(reversed(members))
        self.write_fixture(reordered)
        second = self.poll(cursor=first.next_cursor)
        self.assertEqual(second.events, [])
        self.assertEqual(second.next_cursor, first.next_cursor)

    def test_comment_pagination_stops_at_total_pages(self) -> None:
        payload = base_fixture()
        payload["comment_pages"] = {
            "pk-synthetic:1001:1": {
                "comments": [{"comment_id": "c1", "content": "one", "created_at": "2026-01-01 00:00:01", "creator": "u1", "file_url": ""}],
                "pagination": {"page_num": 1, "page_size": 1, "total": 2, "total_pages": 2},
            },
            "pk-synthetic:1001:2": {
                "comments": [{"comment_id": "c2", "content": "two", "created_at": "2026-01-01 00:00:02", "creator": "u2", "file_url": ""}],
                "pagination": {"page_num": 2, "page_size": 1, "total": 2, "total_pages": 2},
            },
        }
        self.write_fixture(payload)
        result = self.poll()
        self.assertEqual(len([e for e in result.events if e["kind"] == "comment.created"]), 2)
        comment_calls = [call for call in self.calls() if call[:2] == ["comment", "list"]]
        self.assertEqual(len(comment_calls), 2)

    def test_pagination_limit_fails_instead_of_silent_truncation(self) -> None:
        payload = base_fixture()
        payload["comment_pages"]["pk-synthetic:1001:1"]["pagination"]["total_pages"] = 3
        self.write_fixture(payload)
        config = dict(self.config, comment_max_pages=1)
        with self.assertRaises(AdapterError) as ctx:
            self.poll(config=config)
        self.assertEqual(ctx.exception.code, "incomplete_pagination")

    def test_unauthenticated_fails_with_exit_six_and_no_data_calls(self) -> None:
        payload = base_fixture()
        payload["auth"] = {"authenticated": False}
        self.write_fixture(payload)
        with self.assertRaises(AdapterError) as ctx:
            self.poll()
        self.assertEqual(ctx.exception.exit_code, 6)
        self.assertEqual(self.calls(), [["auth", "status", "--format", "json"]])

    def test_expired_token_from_command_is_classified(self) -> None:
        payload = base_fixture()
        payload["failures"] = {"mywork todo": {"returncode": 1, "stderr": "401 token expired"}}
        self.write_fixture(payload)
        with self.assertRaises(AdapterError) as ctx:
            self.poll()
        self.assertEqual(ctx.exception.exit_code, 6)

    def test_work_item_id_requires_project_key(self) -> None:
        with self.assertRaises(AdapterError) as ctx:
            self.poll(config=dict(self.config, work_item_id="1001"))
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_environment_project_key_overrides_project_config(self) -> None:
        payload = base_fixture()
        payload["todo_pages"] = {"1": {"list": [work_item("pk-env", 1002)], "total": 1}}
        payload["details"] = {"pk-env:1002": detail()}
        payload["details"]["pk-env:1002"]["work_item_attribute"]["work_item_id"] = 1002
        payload["comment_pages"] = {"pk-env:1002:1": {"comments": [], "pagination": {"page_num": 1, "total_pages": 1}}}
        self.write_fixture(payload)
        with patch.dict(os.environ, {"MEEGLE_PROJECT_KEY": "pk-env"}):
            result = self.poll(config=dict(self.config, project_key="pk-config"))
        snapshot = next(e for e in result.events if e["kind"] == "work_item.discovered")
        self.assertEqual(snapshot["payload"]["project_key"], "pk-env")

    def test_cli_once_then_second_run_zero_new(self) -> None:
        shutil.copytree(FIXTURE, self.project, dirs_exist_ok=True)
        definition_path = self.project / ".herdr" / "bots" / "test-bot" / "definition.json"
        definition = json.loads(definition_path.read_text())
        definition["adapter"]["name"] = "feishu"
        definition["permissions"]["network_read"] = True
        definition_path.write_text(json.dumps(definition))
        policy_path = self.project / ".herdr" / "bot-policy.json"
        policy = json.loads(policy_path.read_text())
        policy["allowed_adapters"] = ["feishu"]
        policy["allow_network_read"] = True
        policy_path.write_text(json.dumps(policy))
        config_path = self.project / ".herdr" / "bots" / "test-bot" / "adapter-config.json"
        config_path.write_text(json.dumps(self.config))

        def cli(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *args, "--project-root", str(self.project)], capture_output=True, text=True, check=False)

        approved = cli("approve", "--definition", str(definition_path), "--approval-id", "a1", "--approver-ref", "test")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        first = cli("run", "--bot-id", "test-bot", "--once")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(json.loads(first.stdout)["events_new"], 3)
        second = cli("run", "--bot-id", "test-bot", "--once")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(second.stdout)["events_new"], 0)
        state = json.loads((self.project / ".herdr" / "bots" / "test-bot" / "state" / "state.json").read_text())
        self.assertEqual(state["cursor"], json.loads(first.stdout)["cursor"])


if __name__ == "__main__":
    unittest.main()
