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

    def test_docs_run_validation_inside_plugin_context(self):
        for relative_path in ("README.md", "docs/adoption.md"):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/validate_task.py", text, relative_path)
            self.assertIn("Plugin 流程", text, relative_path)
            self.assertIn("普通终端", text, relative_path)
            self.assertIn("实际安装绝对路径", text, relative_path)
            self.assertLess(
                text.index("/agent-collab:project-collaboration"),
                text.index("${CLAUDE_PLUGIN_ROOT}/scripts/validate_task.py"),
                f"{relative_path} 应先引导加载 Plugin 再执行校验命令",
            )


if __name__ == "__main__":
    unittest.main()
