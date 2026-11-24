import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "project-collaboration"


class PiPackageTest(unittest.TestCase):
    def test_pi_manifest_matches_claude_version_and_resolves_skill(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(package["name"], plugin["name"])
        self.assertEqual(package["version"], plugin["version"])
        self.assertIn("pi-package", package["keywords"])
        self.assertEqual(package["pi"]["skills"], ["./skills/project-collaboration"])
        for relative in package["pi"]["skills"]:
            skill_dir = ROOT / relative
            self.assertTrue(skill_dir.is_dir(), relative)
            self.assertTrue((skill_dir / "SKILL.md").is_file(), relative)

    def test_skill_frontmatter_meets_pi_contract(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        name = re.search(r"(?m)^name:\s*(\S+)\s*$", frontmatter).group(1)
        description = re.search(r"(?m)^description:\s*(.+)$", frontmatter).group(1).strip()
        self.assertEqual(name, SKILL_DIR.name)
        self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)
        self.assertGreater(len(description), 0)
        self.assertLessEqual(len(description), 1024)

    def test_wrappers_run_from_project_working_directory(self):
        commands = [
            ["sh", str(SKILL_DIR / "scripts" / "validate-task.sh"),
             "templates/current-task.json", "--phase", "draft"],
            ["sh", str(SKILL_DIR / "scripts" / "agent-eval.sh"),
             "check", "templates/agent-evaluation.json", "--final"],
        ]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrappers_report_missing_python3(self):
        for wrapper in (SKILL_DIR / "scripts").glob("*.sh"):
            with self.subTest(wrapper=wrapper.name):
                result = subprocess.run(
                    ["/bin/sh", str(wrapper)],
                    env={"PATH": ""},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 127)
                self.assertIn("python3 is required", result.stderr)

    def test_skill_and_project_template_have_runtime_entrypoints(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        agents = (ROOT / "templates" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("<skill_dir>/scripts/validate-task.sh", skill)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/project-collaboration", skill)
        for text in (skill, agents):
            self.assertIn("Claude Code", text)
            self.assertIn("Pi", text)
        self.assertIn("Codex", skill)
        self.assertIn("Codex", agents)
        self.assertIn("/agent-collab:project-collaboration", agents)
        self.assertIn("/skill:project-collaboration", agents)
        self.assertIn("$agent-collab:project-collaboration", agents)

    def test_readme_documents_pi_install_and_skill_invocation(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("pi install git:github.com/xiaobin/agent-collab", readme)
        self.assertIn("/skill:project-collaboration", readme)
        self.assertIn("PI_CODING_AGENT_DIR", readme)


if __name__ == "__main__":
    unittest.main()
