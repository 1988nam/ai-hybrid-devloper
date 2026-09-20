import tempfile
import unittest
from pathlib import Path

from app.core import (
    ControlPlaneError,
    FactoryControl,
    Settings,
    compile_runtime_stories,
    tail_file,
    validate_stories,
)


class StoryValidationTests(unittest.TestCase):
    def test_accepts_minimal_valid_story(self):
        stories = validate_stories(
            [{"id": "s1", "prompt": "Do work", "allowed_paths": ["app/a.py"]}]
        )
        self.assertEqual(stories[0]["id"], "s1")

    def test_rejects_duplicate_ids(self):
        with self.assertRaises(ControlPlaneError):
            validate_stories(
                [
                    {"id": "s1", "prompt": "A", "allowed_paths": ["a"]},
                    {"id": "s1", "prompt": "B", "allowed_paths": ["b"]},
                ]
            )

    def test_design_is_compiled_into_story_prompt(self):
        stories = [{"id": "s1", "prompt": "Story requirement", "allowed_paths": ["a"]}]
        compiled = compile_runtime_stories(stories, "Architecture decision")
        self.assertIn("Architecture decision", compiled[0]["prompt"])
        self.assertIn("Story requirement", compiled[0]["prompt"])
        self.assertEqual(stories[0]["prompt"], "Story requirement")


class FreshRunTests(unittest.TestCase):
    def test_start_run_archives_old_current_pointer_before_spawning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                factory_home=root / "factory",
                state_home=root / "state",
                factory_command="factory",
            )

            class FakeControl(FactoryControl):
                def list_projects(self):
                    return [{"name": "olchangi"}]

                def _job_info(self, project):
                    return None

                def _spawn(self, project, command, *, package=None):
                    self.current_exists_during_spawn = (
                        self.runs_dir / project / "current.json"
                    ).exists()
                    return {
                        "pid": 123,
                        "alive": True,
                        "command": command,
                        "package": package,
                    }

            control = FakeControl(settings)
            current = control.runs_dir / "olchangi" / "current.json"
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_text('{"run_id":"old-run"}\n', encoding="utf-8")

            result = control.start_run(
                "olchangi",
                title="New package",
                design_md="# Design",
                stories=[
                    {
                        "id": "S1",
                        "prompt": "Implement it.",
                        "allowed_paths": ["app/a.js"],
                    }
                ],
            )

            self.assertFalse(control.current_exists_during_spawn)
            self.assertFalse(current.exists())
            archives = list(
                current.parent.glob("current.abandoned-*.json")
            )
            self.assertEqual(len(archives), 1)
            self.assertEqual(result["package"]["story_count"], 1)
            self.assertEqual(result["archived_current"], str(archives[0]))

    def test_start_run_restores_old_current_pointer_if_spawn_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                factory_home=root / "factory",
                state_home=root / "state",
                factory_command="factory",
            )

            class FailingControl(FactoryControl):
                def list_projects(self):
                    return [{"name": "olchangi"}]

                def _job_info(self, project):
                    return None

                def _spawn(self, project, command, *, package=None):
                    raise RuntimeError("spawn failed")

            control = FailingControl(settings)
            current = control.runs_dir / "olchangi" / "current.json"
            current.parent.mkdir(parents=True, exist_ok=True)
            current.write_text('{"run_id":"old-run"}\n', encoding="utf-8")

            with self.assertRaises(RuntimeError):
                control.start_run(
                    "olchangi",
                    title="New package",
                    design_md="# Design",
                    stories=[
                        {
                            "id": "S1",
                            "prompt": "Implement it.",
                            "allowed_paths": ["app/a.js"],
                        }
                    ],
                )

            self.assertTrue(current.exists())
            self.assertIn("old-run", current.read_text(encoding="utf-8"))


class TailTests(unittest.TestCase):
    def test_tail_file_returns_end_of_large_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("A" * 100 + "THE_END", encoding="utf-8")
            result = tail_file(path, max_bytes=16)
            self.assertTrue(result.endswith("THE_END"))
            self.assertLessEqual(len(result.encode("utf-8")), 16)


if __name__ == "__main__":
    unittest.main()
