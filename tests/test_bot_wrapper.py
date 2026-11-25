import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "skills" / "project-collaboration" / "scripts" / "bot.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "bot" / "project"


class BotWrapperTest(unittest.TestCase):
    def test_wrapper_runs_the_python_cli(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir) / "project"
            shutil.copytree(FIXTURE, project)
            result = subprocess.run(
                [
                    "sh",
                    str(WRAPPER),
                    "validate",
                    "--definition",
                    str(project / ".herdr" / "bots" / "test-bot" / "definition.json"),
                    "--project-root",
                    str(project),
                ],
                cwd=project,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("test-bot", result.stdout)

    def test_wrapper_resolves_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project = Path(tempdir) / "project"
            shutil.copytree(FIXTURE, project)
            result = subprocess.run(
                [
                    "sh",
                    str(WRAPPER),
                    "status",
                    "--project-root",
                    str(project),
                    "--bot-id",
                    "test-bot",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["state"], "uninitialized")


if __name__ == "__main__":
    unittest.main()