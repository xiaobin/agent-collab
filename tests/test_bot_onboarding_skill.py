"""Contract tests for the single-entry bot-onboarding skill.

The user-facing surface must stay one command per host; all python commands
live in the skill's references, never in the README quickstart.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "bot-onboarding"
SKILL = SKILL_DIR / "SKILL.md"


class BotOnboardingSkillTest(unittest.TestCase):
    def test_skill_is_registered_for_all_three_hosts(self):
        self.assertTrue(SKILL.is_file(), SKILL)
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn("./skills/bot-onboarding", package["pi"]["skills"])
        for relative in package["pi"]["skills"]:
            self.assertTrue((ROOT / relative / "SKILL.md").is_file(), relative)

    def test_frontmatter_and_entry_commands(self):
        text = SKILL.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name:\s*bot-onboarding\s*$")
        description = re.search(r"(?m)^description:\s*(.+)$", frontmatter).group(1).strip()
        self.assertGreater(len(description), 0)
        self.assertLessEqual(len(description), 1024)
        for command in (
            "/agent-collab:bot-onboarding",
            "/skill:bot-onboarding",
            "$agent-collab:bot-onboarding",
        ):
            self.assertIn(command, text)

    def test_flow_requires_owner_approval_and_interactive_login_stops(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn(".herdr/bot-owner.json", text)
        self.assertIn("bot_onboarding.py", text)
        self.assertIn("references/commands.md", text)
        self.assertTrue((SKILL_DIR / "references" / "commands.md").is_file())
        commands = (SKILL_DIR / "references" / "commands.md").read_text(encoding="utf-8")
        for token in ("plan", "write", "approve", "--approver-ref", "--source-ref"):
            self.assertIn(token, commands)
        # A stopped login or a changed definition must never be auto-approved.
        self.assertIn("approval_invalidated", commands)

    def test_skill_asks_frequency_and_run_mode_before_starting(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in (
            "--interval-minutes",
            "--run-mode",
            "1440",
            "AskUserQuestion",
            "现在就让它在后台常驻吗？",
            "run --once",
            "run --resident",
            "再次触发",
            "bot.py stop",
        ):
            self.assertIn(token, text)
        commands = (SKILL_DIR / "references" / "commands.md").read_text(encoding="utf-8")
        for token in (
            "--interval-minutes",
            "--run-mode",
            "现在就让它在后台常驻吗？",
            "resident.pid",
            "resident.log",
            "run.stop_command",
        ):
            self.assertIn(token, commands)

    def test_readme_shows_only_the_agent_entry(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("### 最小试用")
        end = readme.index("<details>", start)
        quickstart = readme[start:end]
        for command in (
            "/agent-collab:bot-onboarding",
            "/skill:bot-onboarding",
            "$agent-collab:bot-onboarding",
        ):
            self.assertIn(command, quickstart)
        self.assertNotIn("python3", quickstart)
        self.assertNotIn("feishu-trial", quickstart)
        self.assertIn("帮我接入飞书同步", quickstart)

    def test_project_collaboration_points_to_the_single_entry(self):
        text = (ROOT / "skills" / "project-collaboration" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("bot-onboarding", text)
        self.assertNotIn("阶段 1 只支持 `process` 运行时、`fake` 适配器和 `run --once`", text)


if __name__ == "__main__":
    unittest.main()