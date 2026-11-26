import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot.py"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


def comment_event(event_id, content, *, created="2026-01-01 10:00:00", author="Synthetic"):
    return {
        "event_id": f"comment:pk-synthetic:1001:{event_id}",
        "kind": "comment.created",
        "payload": {
            "project_key": "pk-synthetic",
            "work_item_id": "1001",
            "comment_id": event_id,
            "content": content,
            "created_at": created,
            "creator_name": author,
            "creator_user_key": "user-synthetic",
            "mentions_are_structured": False,
            "mentions_text": [],
            "attachments": [],
        },
        "received_at": "2026-01-01T10:00:01+00:00",
        "schema_version": 1,
        "source": {"adapter": "feishu", "project_key": "pk-synthetic", "work_item_id": "1001"},
    }


class TriageCliTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tempdir.name) / "project"
        shutil.copytree(FIXTURE, self.project)
        self.bot_dir = self.project / ".herdr" / "bots" / "test-bot"
        self.definition_path = self.bot_dir / "definition.json"
        self.state = self.bot_dir / "state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.write_health()

    def tearDown(self):
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

    def triage(self, *args, timeout=20):
        return self.cli(
            "triage",
            *args,
            "--project-root",
            str(self.project),
            "--bot-id",
            "test-bot",
            timeout=timeout,
        )

    def write_health(self, **overrides):
        payload = {
            "schema_version": 1,
            "bot_id": "test-bot",
            "state": "ok",
            "adapter_auth_status": "ok",
            "consecutive_failures": 0,
            "last_error": None,
        }
        payload.update(overrides)
        (self.state / "health.json").write_text(json.dumps(payload))

    def write_state(self, **overrides):
        payload = {
            "schema_version": 1,
            "bot_id": "test-bot",
            "state": "running",
            "last_error": None,
            "auth_required": False,
            "last_run_at": "2026-01-01T10:00:00+00:00",
        }
        payload.update(overrides)
        (self.state / "state.json").write_text(json.dumps(payload))

    def write_events(self, events):
        with (self.state / "events.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def set_triage(self, **overrides):
        definition = json.loads(self.definition_path.read_text())
        definition["triage"].update(overrides)
        self.definition_path.write_text(json.dumps(definition, ensure_ascii=False))

    def batch_ids(self, result):
        return [
            token.split("=", 1)[1]
            for line in result.stdout.splitlines()
            for token in line.split()
            if token.startswith("batch_id=")
        ]

    def prepare(self, *args):
        return self.triage("prepare", *args)

    def make_result(
        self,
        event_id,
        batch_id,
        *,
        classification="handoff_or_notice",
        urgency="normal",
        question="通知",
        action="仅记录",
        authorization="none",
        supersedes=None,
        source="agent",
    ):
        result = {
            "event_id": event_id,
            "triage_id": f"tr-{event_id}" + (f"-v2" if supersedes else ""),
            "batch_id": batch_id,
            "triage_policy_version": "triage-v2",
            "classification": classification,
            "source": source,
            "urgency": urgency,
            "rationale": "synthetic rationale",
            "question": question,
            "evidence_refs": [event_id],
            "recommended_action": action,
            "required_authorization": authorization,
            "uncertainties": [],
            "status": "proposed",
        }
        if supersedes:
            result["supersedes"] = supersedes
        return result

    def test_batch_id_cannot_escape_the_batch_directory(self):
        result = self.cli(
            "triage",
            "claim",
            "--bot-id",
            "test-bot",
            "--batch-id",
            "../outside",
            "--attempt-id",
            "attempt-x",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("batch_id is invalid", result.stderr)

    def test_empty_events_create_no_batch(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertFalse((self.state / "triage" / "batches").exists())

    def test_oversized_events_are_split(self):
        self.write_events([comment_event(str(i), f"note {i}") for i in range(45)])
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        ids = self.batch_ids(result)
        self.assertEqual(len(ids), 3)
        counts = []
        for batch_id in ids:
            batch = json.loads(
                (self.state / "triage" / "batches" / batch_id / "batch.json").read_text()
            )
            counts.append(batch["event_count"])
            self.assertEqual(batch["status"], "pending")
        self.assertEqual(counts, [20, 20, 5])

    def test_interval_waits_until_due(self):
        self.set_triage(batch_interval_seconds=3600, max_batch_events=10)
        self.write_events([comment_event("i1", "one"), comment_event("i2", "two")])
        first = self.prepare()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.batch_ids(first)), 1)
        now = datetime.now(timezone.utc)
        event = comment_event(
            "i3", "three", created=now.strftime("%Y-%m-%d %H:%M:%S")
        )
        event["received_at"] = now.isoformat()
        with (self.state / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        second = self.prepare()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("TRIAGE_WAITING", second.stdout)
        self.assertEqual(self.batch_ids(second), [])

    def test_duplicate_events_are_batched_once(self):
        event = comment_event("dup-1", "same")
        self.write_events([event, event])
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        ids = self.batch_ids(result)
        self.assertEqual(len(ids), 1)
        batch = json.loads((self.state / "triage" / "batches" / ids[0] / "batch.json").read_text())
        self.assertEqual(batch["event_count"], 1)
        second = self.prepare()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "")

    def test_auth_failure_creates_no_batch(self):
        self.write_health(state="paused", adapter_auth_status="auth_required")
        self.write_events([comment_event("auth-1", "should wait")])
        result = self.prepare()
        self.assertEqual(result.returncode, 6)
        self.assertIn("authentication required", result.stderr)
        self.assertFalse((self.state / "triage" / "batches").exists())

    def test_degraded_collector_creates_no_batch(self):
        self.write_health(state="degraded", consecutive_failures=2)
        self.write_events([comment_event("degraded-1", "wait")])
        result = self.prepare()
        self.assertEqual(result.returncode, 5)
        self.assertIn("degraded", result.stderr)
        self.assertFalse((self.state / "triage" / "batches").exists())

    def test_apply_and_supersede(self):
        event = comment_event("apply-1", "请确认这个需求")
        self.write_events([event])
        prepared = self.prepare("--force")
        batch_id = self.batch_ids(prepared)[0]
        claim = self.triage(
            "claim", "--batch-id", batch_id, "--attempt-id", "attempt-1"
        )
        self.assertEqual(claim.returncode, 0, claim.stderr)
        old = self.make_result(
            event["event_id"],
            batch_id,
            classification="clarification_request",
            question="请确认这个需求",
            action="向用户确认范围",
            authorization="user",
        )
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([old], ensure_ascii=False))
        applied = self.triage(
            "apply", "--batch-id", batch_id, "--file", str(results_file)
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        duplicate = self.triage(
            "apply", "--batch-id", batch_id, "--file", str(results_file)
        )
        self.assertEqual(duplicate.returncode, 2)

        correction = self.make_result(
            event["event_id"],
            batch_id,
            classification="needs_more_context",
            question="请确认这个需求",
            action="补业务背景",
            authorization="user",
            supersedes=old["triage_id"],
        )
        results_file.write_text(json.dumps([correction], ensure_ascii=False))
        corrected = self.triage(
            "apply", "--batch-id", batch_id, "--file", str(results_file)
        )
        self.assertEqual(corrected.returncode, 0, corrected.stderr)
        rows = [
            json.loads(line)
            for line in (self.state / "triage" / "results.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 2)
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("补业务背景", report.stdout)
        self.assertNotIn("向用户确认范围", report.stdout)

    def test_report_groups_and_panel_limits(self):
        self.set_triage(human_report_max_items_per_group=2, human_report_max_lines=12)
        events = [
            comment_event(f"r{i}", f"question {i}") for i in range(8)
        ]
        self.write_events(events)
        prepared = self.prepare("--force")
        batch_id = self.batch_ids(prepared)[0]
        self.triage("claim", "--batch-id", batch_id, "--attempt-id", "attempt-limit")
        results = [
            self.make_result(
                events[0]["event_id"],
                batch_id,
                classification="clarification_request",
                urgency="high",
                question="紧急确认",
                action="确认",
                authorization="user",
            ),
            self.make_result(
                events[1]["event_id"],
                batch_id,
                classification="clarification_request",
                question="确认 B",
                action="确认",
                authorization="user",
            ),
            self.make_result(
                events[2]["event_id"],
                batch_id,
                classification="clarification_request",
                question="确认 C",
                action="确认",
                authorization="user",
            ),
            self.make_result(
                events[3]["event_id"],
                batch_id,
                classification="scope_or_contract_change",
                question="可处理",
                action="另开任务",
                authorization="coordinator",
            ),
            self.make_result(
                events[4]["event_id"],
                batch_id,
                classification="needs_more_context",
                question="缺背景",
                action="补上下文",
                authorization="user",
            ),
            self.make_result(
                events[5]["event_id"],
                batch_id,
                classification="handoff_or_notice",
                question="知会",
                action="记录",
                authorization="none",
            ),
            self.make_result(
                events[6]["event_id"],
                batch_id,
                classification="handoff_or_notice",
                question="知会 2",
                action="记录",
                authorization="none",
            ),
            self.make_result(
                events[7]["event_id"],
                batch_id,
                classification="handoff_or_notice",
                question="知会 3",
                action="记录",
                authorization="none",
            ),
        ]
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps(results, ensure_ascii=False))
        applied = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        lines = report.stdout.rstrip("\n").splitlines()
        self.assertLessEqual(len(lines), 12)
        self.assertIn("高优先级=1", report.stdout)
        self.assertIn("高优先级分布：需人决策 1", report.stdout)
        self.assertIn("需人决策（3） 其中 1 项标 [高]", report.stdout)
        self.assertEqual(report.stdout.count("紧急确认"), 1)
        self.assertIn("…未显示：仅知会 3", report.stdout)
        report_path = self.bot_dir / "reports" / "triage" / f"{batch_id}.agent.md"
        self.assertTrue(report_path.exists())
        markdown = report_path.read_text()
        for question in ("紧急确认", "确认 B", "确认 C", "知会 3"):
            self.assertIn(question, markdown)
        self.assertEqual(markdown.count("### "), 8)

    def test_pending_batch_is_not_silent(self):
        self.write_events([comment_event("pending-1", "pending")])
        prepared = self.prepare("--force")
        batch_id = self.batch_ids(prepared)[0]
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("TRIAGE_PENDING", report.stdout)
        self.assertIn(batch_id, report.stdout)

    def test_mixed_report_lists_pending_batches(self):
        self.set_triage(max_batch_events=1)
        events = [comment_event("mix-1", "one"), comment_event("mix-2", "two")]
        self.write_events(events)
        prepared = self.prepare()
        ids = self.batch_ids(prepared)
        self.assertEqual(len(ids), 2)
        first_batch = json.loads(
            (self.state / "triage" / "batches" / ids[0] / "batch.json").read_text()
        )
        result = self.make_result(first_batch["event_ids"][0], ids[0])
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([result], ensure_ascii=False))
        self.assertEqual(
            self.triage("apply", "--batch-id", ids[0], "--file", str(results_file)).returncode,
            0,
        )
        report = self.triage("report")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("待分诊", report.stdout)
        self.assertIn(ids[1], report.stdout)

    def test_disposition_is_appended_once(self):
        event = comment_event("disp-1", "note")
        self.write_events([event])
        prepared = self.prepare("--force")
        batch_id = self.batch_ids(prepared)[0]
        result = self.make_result(event["event_id"], batch_id)
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([result], ensure_ascii=False))
        self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        first = self.triage(
            "dispose",
            "--triage-id",
            result["triage_id"],
            "--action",
            "noted",
            "--actor",
            "local-user",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.triage(
            "dispose",
            "--triage-id",
            result["triage_id"],
            "--action",
            "noted",
            "--actor",
            "local-user",
        )
        self.assertEqual(second.returncode, 2)
        rows = (self.state / "triage" / "dispositions.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)

    def test_duplicate_triage_id_within_one_apply_is_rejected(self):
        events = [comment_event("same-1", "one"), comment_event("same-2", "two")]
        self.write_events(events)
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        first = self.make_result(events[0]["event_id"], batch_id)
        second = self.make_result(events[1]["event_id"], batch_id)
        second["triage_id"] = first["triage_id"]
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([first, second], ensure_ascii=False))
        result = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate triage_id", result.stderr)
        self.assertFalse((self.state / "triage" / "results.jsonl").exists())

    def test_real_auth_state_with_unauthorized_health_is_auth_exit(self):
        self.write_state(
            state="paused",
            auth_required=True,
            last_error="authentication_expired",
        )
        self.write_health(
            state="paused", adapter_auth_status="unauthorized", last_error="authentication_expired"
        )
        self.write_events([comment_event("auth-real", "wait")])
        result = self.prepare()
        self.assertEqual(result.returncode, 6, result.stderr)
        self.assertIn("authentication required", result.stderr)

    def test_missing_or_corrupt_health_fails_closed(self):
        self.write_state()
        self.write_events([comment_event("health-1", "wait")])
        (self.state / "health.json").unlink()
        missing = self.prepare()
        self.assertEqual(missing.returncode, 5, missing.stderr)
        self.assertIn("health is missing", missing.stderr)
        (self.state / "health.json").write_text("{not-json")
        corrupt = self.prepare()
        self.assertEqual(corrupt.returncode, 5, corrupt.stderr)
        self.assertIn("health is unreadable", corrupt.stderr)

    def test_missing_health_with_uninitialized_state_and_events_fails_closed(self):
        self.write_state(state="uninitialized", last_run_at=None)
        self.write_events([comment_event("health-init", "wait")])
        (self.state / "health.json").unlink()
        result = self.prepare()
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("health is missing", result.stderr)

    def test_concurrent_claim_has_one_winner(self):
        self.write_events([comment_event("claim-race", "one")])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        results = []
        lock = threading.Lock()

        def claim(index):
            result = self.triage(
                "claim", "--batch-id", batch_id, "--attempt-id", f"attempt-{index}"
            )
            with lock:
                results.append(result)

        threads = [threading.Thread(target=claim, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(row.returncode for row in results).count(0), 1)
        batch = json.loads(
            (self.state / "triage" / "batches" / batch_id / "batch.json").read_text()
        )
        self.assertEqual(batch["status"], "claimed")
        self.assertIn(batch["attempt_id"], {"attempt-0", "attempt-1"})

    def test_concurrent_apply_has_one_effective_result(self):
        event = comment_event("apply-race", "one")
        self.write_events([event])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        result = self.make_result(event["event_id"], batch_id)
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([result], ensure_ascii=False))
        outcomes = []
        lock = threading.Lock()

        def apply():
            completed = self.triage(
                "apply", "--batch-id", batch_id, "--file", str(results_file)
            )
            with lock:
                outcomes.append(completed)

        threads = [threading.Thread(target=apply) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(row.returncode for row in outcomes).count(0), 1)
        rows = (self.state / "triage" / "results.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 1)

    def test_partial_apply_can_resume_idempotently(self):
        events = [comment_event("partial-1", "one"), comment_event("partial-2", "two")]
        self.write_events(events)
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        first = self.make_result(events[0]["event_id"], batch_id)
        second = self.make_result(events[1]["event_id"], batch_id)
        stored = {**first, "schema_version": 1, "created_at": "2026-01-01T10:00:00+00:00"}
        (self.state / "triage").mkdir(parents=True, exist_ok=True)
        (self.state / "triage" / "results.jsonl").write_text(
            json.dumps(stored, ensure_ascii=False) + "\n"
        )
        batch_path = self.state / "triage" / "batches" / batch_id / "batch.json"
        batch = json.loads(batch_path.read_text())
        batch["status"] = "applying"
        batch_path.write_text(json.dumps(batch))
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps([first, second], ensure_ascii=False))
        resumed = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertIn('"already_applied": 1', resumed.stdout)
        self.assertEqual(len((self.state / "triage" / "results.jsonl").read_text().splitlines()), 2)
        batch = json.loads(batch_path.read_text())
        self.assertEqual(batch["status"], "done")

    def test_prepare_repairs_missing_triage_ready(self):
        self.write_events([comment_event("ready-1", "one")])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        (self.state / "triage" / "ready.jsonl").unlink()
        repaired = self.prepare("--force")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        ready = (self.state / "triage" / "ready.jsonl").read_text()
        self.assertIn(batch_id, ready)

    def test_all_pending_aggregate_report_is_explicit(self):
        self.write_events([comment_event("all-pending-1", "one")])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        report = self.triage("report")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("TRIAGE_PENDING", report.stdout)
        self.assertIn(batch_id, report.stdout)

    def test_exactly_max_events_creates_batch_immediately(self):
        self.set_triage(batch_interval_seconds=3600, max_batch_events=20)
        now = datetime.now(timezone.utc)
        events = []
        for index in range(20):
            event = comment_event(
                f"exact-{index}", f"note {index}", created=now.strftime("%Y-%m-%d %H:%M:%S")
            )
            event["received_at"] = now.isoformat()
            events.append(event)
        self.write_events(events)
        prepared = self.prepare()
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(len(self.batch_ids(prepared)), 1)

    def test_forty_fresh_events_create_two_batches_immediately(self):
        self.set_triage(batch_interval_seconds=3600, max_batch_events=20)
        now = datetime.now(timezone.utc)
        events = []
        for index in range(40):
            event = comment_event(
                f"forty-{index}", f"note {index}", created=now.strftime("%Y-%m-%d %H:%M:%S")
            )
            event["received_at"] = now.isoformat()
            events.append(event)
        self.write_events(events)
        prepared = self.prepare()
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(len(self.batch_ids(prepared)), 2)

    def test_high_priority_is_globally_first(self):
        self.set_triage(human_report_max_items_per_group=5, human_report_max_lines=40)
        events = [comment_event(f"high-{index}", f"q{index}") for index in range(3)]
        self.write_events(events)
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        results = [
            self.make_result(
                events[0]["event_id"],
                batch_id,
                classification="handoff_or_notice",
                urgency="high",
                question="高优知会",
            ),
            self.make_result(
                events[1]["event_id"],
                batch_id,
                classification="clarification_request",
                urgency="high",
                question="普通决策",
            ),
            self.make_result(
                events[2]["event_id"],
                batch_id,
                classification="handoff_or_notice",
                question="普通知会",
            ),
        ]
        results_file = self.project / "results.json"
        results_file.write_text(json.dumps(results, ensure_ascii=False))
        self.assertEqual(
            self.triage("apply", "--batch-id", batch_id, "--file", str(results_file)).returncode,
            0,
        )
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("高优先级=2", report.stdout)
        self.assertIn("高优先级分布：需人决策 1、仅知会 1", report.stdout)
        self.assertIn("仅知会（2） 其中 1 项标 [高]", report.stdout)
        self.assertEqual(report.stdout.count("高优知会"), 1)
        self.assertEqual(report.stdout.count("需人决策"), 2)

    def test_answer_and_notice_classes_replace_legacy_decision_labels(self):
        event = comment_event("answer-1", "Q1：必填。Q2：非必填。收到，按这个执行。")
        self.write_events([event])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        answer = self.make_result(
            event["event_id"], batch_id, classification="answer_to_my_question", urgency="low"
        )
        results_file = self.project / "answer.json"
        results_file.write_text(json.dumps([answer], ensure_ascii=False))
        applied = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        effective = json.loads((self.state / "triage" / "results.jsonl").read_text().splitlines()[0])
        self.assertEqual(effective["classification"], "answer_to_my_question")
        self.assertEqual(effective["source"], "agent")
        for legacy in ("needs_user_confirmation", "local_action_candidate"):
            bad = self.make_result(event["event_id"], batch_id, classification=legacy)
            bad["supersedes"] = effective["triage_id"]
            bad["triage_id"] = f"legacy-{legacy}"
            results_file.write_text(json.dumps([bad], ensure_ascii=False))
            rejected = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("unsupported value", rejected.stderr)

    def test_heuristic_results_are_not_mixed_into_agent_report(self):
        events = [comment_event("agent-1", "agent notice"), comment_event("heur-1", "heuristic notice")]
        self.write_events(events)
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        rows = [
            self.make_result(events[0]["event_id"], batch_id, question="agent notice"),
            self.make_result(events[1]["event_id"], batch_id, source="heuristic", question="heuristic notice"),
        ]
        results_file = self.project / "sources.json"
        results_file.write_text(json.dumps(rows, ensure_ascii=False))
        self.assertEqual(self.triage("apply", "--batch-id", batch_id, "--file", str(results_file)).returncode, 0)
        agent = self.triage("report", "--batch-id", batch_id, "--source", "agent")
        self.assertEqual(agent.returncode, 0, agent.stderr)
        self.assertIn("agent notice", agent.stdout)
        self.assertNotIn("heuristic notice", agent.stdout)
        self.assertIn("启发式结果另 1 条未混入", agent.stdout)
        heuristic = self.triage("report", "--batch-id", batch_id, "--source", "heuristic")
        self.assertEqual(heuristic.returncode, 0, heuristic.stderr)
        self.assertIn("heuristic notice", heuristic.stdout)
        self.assertNotIn("agent notice", heuristic.stdout)
        self.assertIn("完整报告：", agent.stdout)
        self.assertIn(".agent.md", agent.stdout)

    def test_correction_of_delivered_work_requires_high_urgency(self):
        event = comment_event("correction-1", "已交付接口回归：字段缺失，需要返工")
        self.write_events([event])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        bad = self.make_result(
            event["event_id"], batch_id,
            classification="correction_of_delivered_work", urgency="normal",
        )
        results_file = self.project / "correction.json"
        results_file.write_text(json.dumps([bad], ensure_ascii=False))
        rejected = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("default to high", rejected.stderr)

    def test_apply_backfills_author_and_render_strips_mention_metadata(self):
        event = comment_event(
            "mention-1",
            '@Alice<!-- mention:{"blockId":"x","cn_name":"Alice"} --> 请确认字段',
            author="Alice",
        )
        self.write_events([event])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        result = self.make_result(event["event_id"], batch_id, question="请确认字段")
        results_file = self.project / "author.json"
        results_file.write_text(json.dumps([result], ensure_ascii=False))
        applied = self.triage("apply", "--batch-id", batch_id, "--file", str(results_file))
        self.assertEqual(applied.returncode, 0, applied.stderr)
        rows = [
            json.loads(line)
            for line in (self.state / "triage" / "results.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[0]["author"], "Alice")
        self.assertIsInstance(rows[0]["evidence_refs"], list)
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertNotIn("<!--", report.stdout)
        markdown = (self.bot_dir / "reports" / "triage" / f"{batch_id}.agent.md").read_text()
        self.assertNotIn("<!--", markdown)
        self.assertIn("@Alice", markdown)

    def test_report_rejects_string_list_fields(self):
        event = comment_event("bad-list", "通知")
        self.write_events([event])
        batch_id = self.batch_ids(self.prepare("--force"))[0]
        row = self.make_result(event["event_id"], batch_id)
        row["evidence_refs"] = "['" + event["event_id"] + "']"
        rows_path = self.state / "triage" / "results.jsonl"
        rows_path.parent.mkdir(parents=True, exist_ok=True)
        rows_path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
        report = self.triage("report", "--batch-id", batch_id)
        self.assertEqual(report.returncode, 2)
        self.assertIn("must be a JSON array", report.stderr)


if __name__ == "__main__":
    unittest.main()