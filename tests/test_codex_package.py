import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CodexPackageTest(unittest.TestCase):
    def test_codex_manifest_matches_other_runtime_versions(self):
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        pi = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(codex["version"], claude["version"])
        self.assertEqual(pi["version"], claude["version"])
        self.assertEqual(codex["skills"], "./skills/")
        self.assertTrue((ROOT / codex["skills"] / "project-collaboration" / "SKILL.md").is_file())

    def test_codex_marketplace_points_to_the_same_plugin(self):
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(marketplace["name"], "agent-collab")
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "agent-collab")
        self.assertEqual(entry["source"], {"source": "local", "path": "./"})
        self.assertTrue((ROOT / entry["source"]["path"] / ".codex-plugin" / "plugin.json").is_file())

    def test_runtime_entrypoints_are_documented(self):
        agents = (ROOT / "templates" / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / "skills" / "project-collaboration" / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Codex", agents)
        self.assertIn("Codex", skill)
        self.assertIn("codex plugin add agent-collab@agent-collab", readme)
        self.assertIn("CODEX_HOME", readme)
        for text in (agents, readme):
            self.assertIn("$agent-collab:project-collaboration", text)

    def test_codex_verifier_uses_an_isolated_home_and_no_model(self):
        verifier = (ROOT / "scripts" / "verify_codex.sh").read_text(encoding="utf-8")
        self.assertIn('CODEX_HOME="$TMP_DIR/codex-home"', verifier)
        self.assertIn("codex debug prompt-input", verifier)
        self.assertIn(
            "PLUGIN_ADD_JSON=$(codex plugin add agent-collab@agent-collab --json)",
            verifier,
        )
        self.assertIn('tee "$TMP_DIR/plugin-add.json"', verifier)
        self.assertNotIn("codex plugin add agent-collab@agent-collab --json >", verifier)
        self.assertNotIn("codex exec", verifier)
        for excluded in (".omc", ".herdr", ".git", "__pycache__", ".DS_Store"):
            self.assertIn(excluded, verifier)
        self.assertIn('"$ROOT/.codex-plugin/plugin.json"', verifier)
        self.assertNotIn('"$ROOT/.claude-plugin/plugin.json"', verifier)
        self.assertIn("expected_version", verifier)
        self.assertNotRegex(verifier, r'assert install\["version"\] == "\d+\.\d+\.\d+"')
        self.assertNotRegex(verifier, r"agent-collab/\d+\.\d+\.\d+/skills")


if __name__ == "__main__":
    unittest.main()
