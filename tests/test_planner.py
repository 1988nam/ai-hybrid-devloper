import json
import unittest

from app.planner import (
    PlannerError,
    build_planner_prompt,
    parse_model_json,
    validate_planner_result,
)


class PlannerJsonTests(unittest.TestCase):
    def test_parse_model_json_accepts_raw_object(self):
        result = parse_model_json(
            json.dumps(
                {
                    "title": "Feature",
                    "design_markdown": "# Design",
                    "stories": [],
                }
            )
        )
        self.assertEqual(result["title"], "Feature")

    def test_parse_model_json_accepts_markdown_fence(self):
        result = parse_model_json(
            """```json
{"title":"Feature","design_markdown":"# Design","stories":[]}
```"""
        )
        self.assertEqual(result["design_markdown"], "# Design")


class PlannerValidationTests(unittest.TestCase):
    def test_validates_and_normalizes_frontier_result(self):
        result = validate_planner_result(
            {
                "title": "  Account switching  ",
                "design_markdown": "  # Design\nDecision  ",
                "stories": [
                    {
                        "id": "story-001",
                        "title": "  Add guard  ",
                        "prompt": "  Implement the guard.  ",
                        "allowed_paths": [" shared/auth.js ", " tests/auth.test.mjs "],
                        "verify_commands": [" node --test tests/auth.test.mjs "],
                    }
                ],
            }
        )

        self.assertEqual(result["title"], "Account switching")
        self.assertEqual(result["stories"][0]["title"], "Add guard")
        self.assertEqual(
            result["stories"][0]["allowed_paths"],
            ["shared/auth.js", "tests/auth.test.mjs"],
        )
        self.assertEqual(
            result["stories"][0]["verify_commands"],
            ["node --test tests/auth.test.mjs"],
        )

    def test_rejects_duplicate_story_ids(self):
        story = {
            "id": "story-001",
            "title": "Story",
            "prompt": "Implement it",
            "allowed_paths": ["app/a.py"],
        }

        with self.assertRaises(PlannerError):
            validate_planner_result(
                {
                    "title": "Feature",
                    "design_markdown": "# Design",
                    "stories": [story, dict(story)],
                }
            )

    def test_rejects_empty_allowed_paths(self):
        with self.assertRaises(PlannerError):
            validate_planner_result(
                {
                    "title": "Feature",
                    "design_markdown": "# Design",
                    "stories": [
                        {
                            "id": "story-001",
                            "title": "Story",
                            "prompt": "Implement it",
                            "allowed_paths": [],
                        }
                    ],
                }
            )


class PlannerPromptTests(unittest.TestCase):
    def test_prompt_requires_repo_grounding_and_read_only_planning(self):
        prompt = build_planner_prompt(
            "Improve account switching without regressing backups.",
            "olchangi",
        )

        self.assertIn("olchangi", prompt)
        self.assertIn("Improve account switching", prompt)
        self.assertIn("Inspect the CURRENT repository", prompt)
        self.assertIn("Do not edit, create, delete", prompt)
        self.assertIn("Never invent an existing path", prompt)
        self.assertIn("prohibit deleting, skipping, weakening", prompt)


if __name__ == "__main__":
    unittest.main()
