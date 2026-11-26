"""Release manifests must agree on one version."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionTest(unittest.TestCase):
    def test_version_is_0_4_13_everywhere(self):
        for relative in (".claude-plugin/plugin.json", "package.json", ".codex-plugin/plugin.json"):
            payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], "0.4.13", relative)
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(market["plugins"][0]["version"], "0.4.13")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.4.13]", changelog)


if __name__ == "__main__":
    unittest.main()
