import tempfile
import unittest
from pathlib import Path

from app.core import ControlPlaneError, compile_runtime_stories, tail_file, validate_stories


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
