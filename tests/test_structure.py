import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PluginStructureTest(unittest.TestCase):
    def test_manifest_and_marketplace_versions_match(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = marketplace["plugins"][0]
        self.assertEqual(manifest["name"], entry["name"])
        self.assertEqual(manifest["version"], entry["version"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_skill_has_name_description_and_local_references(self):
        skill_dir = ROOT / "skills" / "project-collaboration"
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name:\s*project-collaboration\s*$")
        self.assertRegex(frontmatter, r"(?m)^description:\s*\S+")
        links = re.findall(r"\]\(([^)]+)\)", text)
        local_links = [link for link in links if "://" not in link and not link.startswith("#")]
        self.assertGreaterEqual(len(local_links), 5)
        for link in local_links:
            self.assertTrue((skill_dir / link).is_file(), link)

    def test_all_skill_references_are_linked(self):
        skill_dir = ROOT / "skills" / "project-collaboration"
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        local_links = {
            link
            for link in re.findall(r"\]\(([^)]+)\)", text)
            if "://" not in link and not link.startswith("#")
        }
        references = sorted((skill_dir / "references").glob("*.md"))
        self.assertGreaterEqual(len(references), 6)
        for reference in references:
            relative = f"references/{reference.name}"
            self.assertIn(relative, local_links, f"orphan reference: {relative}")

    def test_changelog_contains_manifest_version(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{manifest['version']}]", changelog)

    def test_templates_do_not_contain_project_runtime_state(self):
        progress = (ROOT / "templates" / "PROGRESS.md").read_text(encoding="utf-8")
        self.assertIn("恢复入口", progress)
        self.assertNotIn("jbinfo-iot", progress)
        self.assertNotIn("attempt-0", progress)

    def test_progress_template_records_in_flight_tasks_and_resources(self):
        progress = (ROOT / "templates" / "PROGRESS.md").read_text(encoding="utf-8")
        self.assertIn("在飞任务", progress)
        self.assertIn("恢复入口", progress)
        for field in [
            "task_id",
            "attempt_id",
            "agent",
            "session",
            "写范围",
            "pane",
            "worktree",
            "后台命令",
            "状态",
        ]:
            self.assertIn(field, progress, field)

    def test_agent_evaluation_template_contract(self):
        record = json.loads(
            (ROOT / "templates" / "agent-evaluation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["mode"], "project")
        self.assertEqual(record["stage"], "final")
        self.assertIsNone(record["benchmark"])
        self.assertNotEqual(record["agent"], record["evaluator"])
        self.assertNotEqual(record["agent"], record["reviewer"])
        self.assertEqual(
            set(record["gates"]),
            {
                "authorization",
                "external_impact",
                "write_scope",
                "evidence_integrity",
                "coordinator_rerun",
                "independent_review",
                "business_acceptance",
            },
        )
        self.assertEqual(
            set(record["dimensions"]),
            {
                "goal_alignment",
                "implementation_correctness",
                "verification_discipline",
                "communication_handoff",
                "recovery",
            },
        )

    def assert_self_install_prompt(self, block, required_text):
        confirmation = "开始前先向我确认，不要自行假定"
        self.assertIn(confirmation, block)
        self.assertLess(block.index(confirmation), block.index("按顺序执行"))
        self.assertNotIn("安装范围：user", block)
        self.assertNotIn("期望版本：", block)
        self.assertNotRegex(block, r"--scope\s+user\b")
        self.assertNotRegex(block, r"@v\d+\.\d+\.\d+")
        self.assertNotRegex(block, r"--ref\s+v\d+\.\d+\.\d+")
        self.assertIn("python3 --version", block)
        for text in required_text:
            self.assertIn(text, block)

    def test_self_install_prompts_require_scope_confirmation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("## 让 agent 自行安装"):readme.index("## 接入项目")]
        blocks = re.findall(r"```text\n(.*?)```", section, re.DOTALL)
        self.assertEqual(len(blocks), 3)
        requirements = [
            ("<确认的范围>", "user、project 或 local"),
            ("<确认的 tag>", "用户级，还是项目级"),
            ("<确认的 tag>", "只支持用户级"),
        ]
        for block, required_text in zip(blocks, requirements):
            with self.subTest(required_text=required_text):
                self.assert_self_install_prompt(block, required_text)
                with self.assertRaises(AssertionError):
                    self.assert_self_install_prompt(
                        block.replace("开始前先向我确认，不要自行假定", ""), required_text
                    )
                hardcoded = block.replace("<确认的范围>", "user").replace(
                    "<确认的 tag>", "v0.3.0"
                )
                with self.assertRaises(AssertionError):
                    self.assert_self_install_prompt(hardcoded, required_text)

    def test_docs_run_validation_with_runtime_specific_skill_paths(self):
        claude_command = "${CLAUDE_PLUGIN_ROOT}/skills/project-collaboration/scripts/validate-task.sh"
        shared_command = "<skill_dir>/scripts/validate-task.sh"
        for relative_path, heading in (("README.md", "## 接入项目"), ("docs/adoption.md", "## 新项目")):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            section = text[text.index(heading):]
            self.assertIn(claude_command, section, relative_path)
            self.assertIn(shared_command, section, relative_path)
            self.assertIn("Plugin 流程", section, relative_path)
            self.assertIn("普通终端", section, relative_path)
            self.assertIn("实际安装绝对路径", section, relative_path)
            for entrypoint, command in (
                ("/agent-collab:project-collaboration", claude_command),
                ("/skill:project-collaboration", shared_command),
                ("$agent-collab:project-collaboration", shared_command),
            ):
                self.assertLess(
                    section.index(entrypoint),
                    section.index(command),
                    f"{relative_path} 应先引导加载 {entrypoint} 再执行校验命令",
                )


if __name__ == "__main__":
    unittest.main()
