"""Offline checks for the feishu onboarding templates.

These tests fill the shipped templates the way the onboarding flow does and
assert the result passes structural + project-policy validation. They run
entirely offline: no meegle call, no network, no Herdr.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bot_schema  # noqa: E402

DEFINITION = "bot-definition-feishu.json"
POLICY = "bot-policy-feishu.json"
ADAPTER = "adapter-config.example.json"

PLACEHOLDERS = {
    "REPLACE-WITH-GOAL-REF": "goal:panel-task-42",
    "REPLACE-WITH-OWNER-REF": "owner:alice",
    "REPLACE-WITH-WORKSPACE-LABEL": "demo-workspace",
    "REPLACE-WITH-PROJECT-ROOT": "/tmp/demo-project",
}


def load(name: str) -> dict:
    return json.loads((ROOT / "templates" / name).read_text(encoding="utf-8"))


class OnboardingTemplateTest(unittest.TestCase):
    def test_feishu_definition_exposes_explicit_placeholders(self):
        template = load(DEFINITION)
        raw = json.dumps(template)
        for token in PLACEHOLDERS:
            self.assertIn(token, raw, f"{token} must stay explicit for onboarding to fill")
        self.assertNotIn("replace-me", raw)
        self.assertEqual(template["bot_id"], "feishu-sync")
        self.assertEqual(template["adapter"]["name"], "feishu")
        self.assertTrue(template["permissions"]["network_read"])
        self.assertFalse(template["permissions"]["network_write"])
        self.assertEqual(template["permissions"]["external_actions"], "deny")

    def test_feishu_policy_allows_read_only_feishu_adapter(self):
        policy = load(POLICY)
        self.assertIn("feishu", policy["allowed_adapters"])
        self.assertTrue(policy["allow_network_read"])
        self.assertFalse(policy["allow_network_write"])
        self.assertTrue(policy["require_approval_for_start"])

    def test_adapter_example_only_carries_identity_fields(self):
        adapter = load(ADAPTER)
        self.assertIn("project_key", adapter)
        self.assertIn("expected_user_key", adapter)

    def test_filled_templates_validate_against_policy(self):
        raw = (ROOT / "templates" / DEFINITION).read_text(encoding="utf-8")
        filled = raw
        for token, value in PLACEHOLDERS.items():
            filled = filled.replace(token, value)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".herdr" / "bots" / "feishu-sync").mkdir(parents=True)
            shutil.copy(ROOT / "templates" / POLICY, project / ".herdr" / "bot-policy.json")
            definition_path = project / ".herdr" / "bots" / "feishu-sync" / "definition.json"
            definition_path.write_text(filled, encoding="utf-8")
            adapter = load(ADAPTER)
            adapter.update({"project_key": "demo-project", "expected_user_key": "demo-user"})
            (project / ".herdr" / "bots" / "feishu-sync" / "adapter-config.json").write_text(
                json.dumps(adapter, ensure_ascii=False), encoding="utf-8"
            )
            result = bot_schema.validate_definition(definition_path, project)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.definition["bot_id"], "feishu-sync")


if __name__ == "__main__":
    unittest.main()