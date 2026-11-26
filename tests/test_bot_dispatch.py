import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


def comment_event(event_id):
    return {
        "event_id": f"comment:pk-synthetic:1001:{event_id}",
        "kind": "comment.created",
        "payload": {
            "project_key": "pk-synthetic",
            "work_item_id": "1001",
            "comment_id": event_id,
            "content": f"comment {event_id}",
            "created_at": "2026-01-01 10:00:00",
            "creator_name": "Synthetic",
            "creator_user_key": "user-synthetic",
            "mentions_are_structured": False,
            "mentions_text": [],
            "attachments": [],
        },
        "received_at": "2026-01-01T10:00:01+00:00",
        "schema_version": 1,
        "source": {"adapter": "feishu", "project_key": "pk-synthetic", "work_item_id": "1001"},
    }


FAKE_HERDR = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
base = Path(__file__).resolve().parent
state_path = base / "fake-herdr-state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
args = sys.argv[1:]
if args[:2] == ["workspace", "list"]:
    if state.get("mode") == "unavailable":
        print("herdr unavailable", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"result": {"workspaces": [
        {"workspace_id": "w9", "label": "test-ws"}
    ]}}))
    raise SystemExit(0)
if args[:2] == ["pane", "list"]:
    panes = state.get("panes")
    if not isinstance(panes, list):
        panes = [{
            "pane_id": "w9:p1",
            "workspace_id": "w9",
            "cwd": state.get("cwd", str(base)),
            "agent": "claude",
            "agent_status": state.get("status", "idle"),
        }]
    print(json.dumps({"result": {"panes": panes}}))
    raise SystemExit(0)
if args[:2] == ["pane", "process-info"]:
    pane_id = args[args.index("--pane") + 1]
    pids = state.get("pane_pids", {}).get(pane_id, [])
    print(json.dumps({"result": {"process_info": {
        "foreground_process_group_id": pids[0] if pids else None,
        "foreground_processes": [{"pid": pid} for pid in pids],
    }}}))
    raise SystemExit(0)
if args[:2] == ["pane", "run"]:
    prompt = args[3]
    (base / "fake-herdr.log").open("a", encoding="utf-8").write(prompt + "\n---\n")
    mode = state.get("mode", "write")
    if mode == "fail":
        print("pane run failed", file=sys.stderr)
        raise SystemExit(1)
    state["calls"] = state.get("calls", 0) + 1
    state["status"] = "working"
    state_path.write_text(json.dumps(state))
    if mode in {"write", "invalid"} or (mode == "retry" and state["calls"] > 1):
        batch_file = Path(prompt.split("batch_file: ", 1)[1].splitlines()[0])
        output_file = Path(prompt.split("output_file: ", 1)[1].splitlines()[0])
        batch = json.loads(batch_file.read_text())
        if mode == "invalid":
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text("not-json\n")
            print(json.dumps({"result": {"pane_id": args[2]}}))
            raise SystemExit(0)
        rows = []
        for event in batch["events"]:
            event_id = event["event_id"]
            rows.append({
                "event_id": event_id,
                "triage_id": "tr-" + event_id.split(":")[-1],
                "batch_id": batch["batch_id"],
                "triage_policy_version": "triage-v2",
                "classification": "handoff_or_notice",
                "source": "agent",
                "urgency": "low",
                "rationale": "Synthetic fake agent classification.",
                "evidence_refs": [event_id],
                "recommended_action": "No action required.",
                "required_authorization": "none",
                "uncertainties": [],
                "status": "proposed",
            })
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
    print(json.dumps({"result": {"pane_id": args[2]}}))
    raise SystemExit(0)
print("unsupported", file=sys.stderr)
raise SystemExit(2)
'''


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.bot_dir = self.project / ".herdr" / "bots" / "test-bot"
        self.state = self.bot_dir / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "health.json").write_text(json.dumps({
            "schema_version": 1,
            "bot_id": "test-bot",
            "state": "ok",
            "adapter_auth_status": "ok",
            "consecutive_failures": 0,
            "last_error": None,
        }))
        self.herdr = self.project / "fake-herdr"
        self.herdr.write_text(FAKE_HERDR)
        self.herdr.chmod(0o755)
        self.definition_path = self.bot_dir / "definition.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def cli(self, *args, timeout=20, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )

    def set_triage(self, **overrides):
        definition = json.loads(self.definition_path.read_text())
        definition["triage"].update(overrides)
        self.definition_path.write_text(json.dumps(definition))

    def dispatch(self, *args, timeout=20, env=None):
        return self.cli(
            "triage", "dispatch",
            "--project-root", str(self.project),
            "--bot-id", "test-bot",
            *args,
            timeout=timeout,
            env=env,
        )

    def prepare(self):
        self.set_triage(max_batch_events=1, batch_interval_seconds=60)
        (self.state / "events.jsonl").write_text(json.dumps(comment_event("d1")) + "\n")
        result = self.cli(
            "triage", "prepare",
            "--project-root", str(self.project),
            "--bot-id", "test-bot",
            "--force",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split("batch_id=", 1)[1].split()[0]

    def configure(
        self,
        *,
        mode="write",
        status="idle",
        cwd=None,
        timeout=1,
        panes=None,
        exclude_panes=None,
        allow_pane_labels=None,
    ):
        state = {
            "mode": mode,
            "status": status,
            "cwd": str(cwd or self.project),
        }
        if panes is not None:
            state["panes"] = panes
        (self.project / "fake-herdr-state.json").write_text(json.dumps(state))
        dispatch = {
            "mode": "herdr",
            "workspace_label": "test-ws",
            "cwd_marker": str(self.project),
            "herdr_bin": str(self.herdr),
            "ack_timeout_seconds": timeout,
        }
        if exclude_panes is not None:
            dispatch["exclude_panes"] = exclude_panes
        if allow_pane_labels is not None:
            dispatch["allow_pane_labels"] = allow_pane_labels
        self.set_triage(triage_trigger="herdr_batch", dispatch=dispatch)

    def test_manual_trigger_is_disabled(self):
        self.set_triage(triage_trigger="manual")
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("disabled", result.stdout)

    def test_missing_dispatch_config_stays_pending(self):
        self.set_triage(triage_trigger="herdr_batch")
        batch_id = self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not configured", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")

    def test_busy_pane_waits_without_claiming(self):
        self.configure(status="working")
        batch_id = self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("working", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")
        self.assertFalse((self.project / "fake-herdr.log").exists())

    def test_unavailable_herdr_stays_pending(self):
        self.configure(mode="unavailable")
        self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pending", result.stdout)

    def test_idle_pane_receives_prompt_and_results_are_applied(self):
        self.configure()
        batch_id = self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"state": "applied"', result.stdout)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["state"], "applied")
        self.assertEqual(record["pane_id"], "w9:p1")
        self.assertEqual(record["classification_counts"], {"handoff_or_notice": 1})
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "done")
        self.assertTrue((self.state / "triage" / "output" / f"{batch_id}.jsonl").exists())
        log = (self.project / "fake-herdr.log").read_text()
        self.assertIn(batch_id, log)
        self.assertNotIn("MEEGLE", log)
        self.assertNotIn("secret", log.lower())
        self.assertNotIn("comment d1", log)

    def test_duplicate_dispatch_does_not_run_pane_twice(self):
        self.configure()
        self.prepare()
        first = self.dispatch(timeout=5)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.dispatch(timeout=5)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already dispatched", second.stdout)
        self.assertEqual((self.project / "fake-herdr.log").read_text().count("TRIAGE_BATCH"), 1)

    def test_ack_timeout_records_missing_events(self):
        self.configure(mode="no_output")
        batch_id = self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 5)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["state"], "timeout")
        self.assertTrue(record["missing_event_ids"])
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "claimed")

    def test_timeout_retries_only_missing_events(self):
        self.configure(mode="retry")
        self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["state"], "applied")
        self.assertTrue(record["retried_missing"])
        log = (self.project / "fake-herdr.log").read_text()
        self.assertIn("RETRY_ONLY_MISSING", log)

    def test_invalid_output_is_not_reported_as_triaged(self):
        self.configure(mode="invalid")
        self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 5)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["state"], "invalid_output")

    def test_self_pane_is_never_dispatched(self):
        self.configure(
            panes=[{
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(self.project),
                "agent": "claude",
                "agent_status": "idle",
            }],
        )
        batch_id = self.prepare()
        result = self.dispatch(env={"HERDR_PANE_ID": "w9:p1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("only the dispatching pane is available", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")
        self.assertFalse((self.project / "fake-herdr.log").exists())

    def test_exclude_panes_keeps_batch_pending_with_config_reason(self):
        self.configure(
            panes=[{
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(self.project),
                "agent": "claude",
                "agent_status": "idle",
            }],
            exclude_panes=["w9:p1"],
        )
        batch_id = self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("excluded by configuration", result.stdout)
        self.assertNotIn("only the dispatching pane", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")
        self.assertFalse((self.project / "fake-herdr.log").exists())

    def test_pane_pid_only_self_is_reported_as_self(self):
        self.configure(
            panes=[{
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(self.project),
                "agent": "claude",
                "agent_status": "idle",
            }],
        )
        state_path = self.project / "fake-herdr-state.json"
        state = json.loads(state_path.read_text())
        state["pane_pids"] = {"w9:p1": [4242]}
        state_path.write_text(json.dumps(state))
        (self.state / "pane.pid").write_text("4242")
        batch_id = self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("only the dispatching pane is available", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")
        self.assertFalse((self.project / "fake-herdr.log").exists())

    def test_self_pane_skips_to_another_idle_pane(self):
        self.configure(
            panes=[
                {
                    "pane_id": "w9:p1",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                },
                {
                    "pane_id": "w9:p2",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                },
            ],
        )
        self.prepare()
        result = self.dispatch(timeout=5, env={"HERDR_PANE_ID": "w9:p1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["state"], "applied")
        self.assertEqual(record["pane_id"], "w9:p2")

    def test_pane_pid_file_excludes_source_pane(self):
        self.configure(
            panes=[
                {
                    "pane_id": "w9:p1",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                },
                {
                    "pane_id": "w9:p2",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                },
            ],
        )
        state_path = self.project / "fake-herdr-state.json"
        state = json.loads(state_path.read_text())
        state["pane_pids"] = {"w9:p1": [4242]}
        state_path.write_text(json.dumps(state))
        (self.state / "pane.pid").write_text("4242")
        self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["pane_id"], "w9:p2")

    def test_non_agent_pane_is_skipped(self):
        self.configure(
            panes=[{
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(self.project),
                "agent": "shell",
                "agent_status": "idle",
            }],
        )
        batch_id = self.prepare()
        result = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no actionable agent pane", result.stdout)
        batch = json.loads((self.state / "triage" / "batches" / batch_id / "batch.json").read_text())
        self.assertEqual(batch["status"], "pending")
        self.assertFalse((self.project / "fake-herdr.log").exists())

    def test_allow_pane_labels_limits_targets(self):
        self.configure(
            panes=[
                {
                    "pane_id": "w9:p1",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                    "terminal_title_stripped": "other",
                },
                {
                    "pane_id": "w9:p2",
                    "workspace_id": "w9",
                    "cwd": str(self.project),
                    "agent": "claude",
                    "agent_status": "idle",
                    "terminal_title_stripped": "allowed",
                },
            ],
            allow_pane_labels=["allowed"],
        )
        self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["pane_id"], "w9:p2")

    def test_allow_pane_labels_matches_any_pane_label(self):
        self.configure(
            panes=[{
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(self.project),
                "agent": "claude",
                "agent_status": "idle",
                "label": "display-name",
                "terminal_title_stripped": "allowed-title",
            }],
            allow_pane_labels=["allowed-title"],
        )
        self.prepare()
        result = self.dispatch(timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.state / "triage" / "dispatch.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["pane_id"], "w9:p1")

    def test_dispatch_pane_lists_must_be_string_lists(self):
        self.set_triage(
            triage_trigger="herdr_batch",
            dispatch={
                "mode": "herdr",
                "workspace_label": "test-ws",
                "cwd_marker": str(self.project),
                "herdr_bin": str(self.herdr),
                "allow_pane_labels": "allowed",
            },
        )
        result = self.cli(
            "validate",
            "--definition", str(self.definition_path),
            "--project-root", str(self.project),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("allow_pane_labels", result.stderr)

    def test_pane_id_is_rejected_in_configuration(self):
        self.set_triage(
            triage_trigger="herdr_batch",
            dispatch={
                "mode": "herdr",
                "workspace_label": "w4:p1",
                "cwd_marker": str(self.project),
                "herdr_bin": str(self.herdr),
            },
        )
        result = self.cli(
            "validate",
            "--definition", str(self.definition_path),
            "--project-root", str(self.project),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("workspace label", result.stderr)


if __name__ == "__main__":
    unittest.main()