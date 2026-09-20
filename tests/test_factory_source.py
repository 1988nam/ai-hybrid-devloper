import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from factory.runner import Runner


ROOT = Path(__file__).resolve().parents[1]


class FactoryBranchNamespaceTests(unittest.TestCase):
    def make_runner(self, integration_branch: str) -> Runner:
        runner = Runner.__new__(Runner)
        runner.args = SimpleNamespace(integration_branch=integration_branch)
        return runner

    def test_story_branch_is_namespaced_by_factory_run(self):
        runner = self.make_runner("factory/olchangi/20260920-224206")

        self.assertEqual(
            runner.branch_name({"id": "S1"}),
            "local-agent/factory-olchangi-20260920-224206/S1",
        )

    def test_same_story_id_is_unique_across_runs(self):
        first = self.make_runner("factory/olchangi/20260920-224206")
        second = self.make_runner("factory/olchangi/20260920-231500")

        self.assertNotEqual(
            first.branch_name({"id": "S1"}),
            second.branch_name({"id": "S1"}),
        )


class FactoryHomeTests(unittest.TestCase):
    def test_factory_module_honors_factory_home_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["FACTORY_HOME"] = tmp
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from factory import factory; print(factory.FACTORY_HOME)",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            self.assertEqual(
                Path(proc.stdout.strip()),
                Path(tmp).resolve(),
            )


class InstallerContractTests(unittest.TestCase):
    def test_installer_syncs_tracked_factory_without_runtime_state(self):
        script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

        self.assertIn('install -m 0755 "$source_path" "$FACTORY_RUNTIME/$name"', script)
        self.assertIn('mkdir -p "$FACTORY_RUNTIME/projects"', script)
        self.assertIn('mkdir -p "$FACTORY_RUNTIME/runs"', script)
        self.assertIn('mkdir -p "$FACTORY_RUNTIME/inbox"', script)
        self.assertNotIn('rm -rf "$FACTORY_RUNTIME"', script)


if __name__ == "__main__":
    unittest.main()
