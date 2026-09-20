import json
import tempfile
import unittest
from pathlib import Path

from app.planner import (
    LOCAL_STORY_HARD_MAX_PATHS,
    PlannerError,
    PlannerService,
    build_codex_exec_command,
    build_gemini_login_script,
    build_planner_prompt,
    build_story_slicer_prompt,
    parse_model_json,
    story_budget_violations,
    validate_planner_result,
)


class PlannerSchemaTests(unittest.TestCase):
    def test_every_story_property_is_required_for_strict_structured_output(self):
        from app.planner import PLANNER_SCHEMA

        story_schema = PLANNER_SCHEMA["properties"]["stories"]["items"]
        self.assertEqual(
            set(story_schema["required"]),
            set(story_schema["properties"]),
        )

    def test_verify_commands_is_nullable_to_preserve_optional_semantics(self):
        from app.planner import PLANNER_SCHEMA

        verify_schema = (
            PLANNER_SCHEMA["properties"]["stories"]["items"]["properties"][
                "verify_commands"
            ]
        )
        self.assertEqual(verify_schema["type"], ["array", "null"])


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




class StoryBudgetTests(unittest.TestCase):
    def test_oversized_story_and_escaped_paths_are_rejected_by_budget(self):
        stories = [
            {
                "id": "S1",
                "title": "Too broad",
                "prompt": "Do too many things.",
                "allowed_paths": [
                    "a.js",
                    "b.js",
                    "c.js",
                    "d.js",
                    "e.js",
                    "f.js",
                    "apps/dachangi/sw\\.js",
                ],
            }
        ]

        violations = story_budget_violations(stories)

        self.assertTrue(
            any("exceeds hard max" in item for item in violations),
            violations,
        )
        self.assertTrue(
            any("without regex/shell escaping" in item for item in violations),
            violations,
        )

    def test_planner_prompt_explains_local_story_budget(self):
        prompt = build_planner_prompt("Implement a feature.", "olchangi")

        self.assertIn("Target 2-4 editable files per story", prompt)
        self.assertIn(
            f"HARD MAXIMUM: {LOCAL_STORY_HARD_MAX_PATHS} allowed_paths",
            prompt,
        )
        self.assertIn("EDIT PERMISSION LIST", prompt)
        self.assertIn("Never create a knowingly broken intermediate migration", prompt)
        self.assertIn("not apps/dachangi/sw\\.js", prompt)

    def test_slicer_prompt_preserves_architecture_and_explains_failure(self):
        prompt = build_story_slicer_prompt(
            "Implement a feature.",
            "olchangi",
            "Locked title",
            "# Locked design",
            [
                {
                    "id": "S1",
                    "title": "Large story",
                    "prompt": "Do everything",
                    "allowed_paths": ["a", "b", "c", "d", "e", "f"],
                }
            ],
            ["S1: 6 editable paths exceeds hard max 5"],
        )

        self.assertIn("LOCAL-EXECUTION STORY SLICER", prompt)
        self.assertIn("Locked title", prompt)
        self.assertIn("# Locked design", prompt)
        self.assertIn("S1: 6 editable paths exceeds hard max 5", prompt)
        self.assertIn("Only the story partitioning may change", prompt)


class AutoResliceTests(unittest.TestCase):
    def test_generate_automatically_reslices_oversized_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            state_home = Path(tmp) / "state"
            repo.mkdir()

            for name in ["a.js", "b.js", "c.js", "d.js", "e.js", "f.js"]:
                (repo / name).write_text("// fixture\n", encoding="utf-8")

            initial = {
                "title": "Feature",
                "design_markdown": "# Architecture",
                "stories": [
                    {
                        "id": "S1",
                        "title": "Too large",
                        "prompt": "Implement everything.",
                        "allowed_paths": [
                            "a.js",
                            "b.js",
                            "c.js",
                            "d.js",
                            "e.js",
                            "f.js",
                        ],
                    }
                ],
            }
            sliced = {
                "title": "Model tried to rename this",
                "design_markdown": "# Model tried to redesign this",
                "stories": [
                    {
                        "id": "S1",
                        "title": "Part one",
                        "prompt": "Implement first part.",
                        "allowed_paths": ["a.js", "b.js", "c.js"],
                    },
                    {
                        "id": "S2",
                        "title": "Part two",
                        "prompt": "Implement second part.",
                        "allowed_paths": ["d.js", "e.js", "f.js"],
                    },
                ],
            }

            class FakePlanner(PlannerService):
                def __init__(self):
                    super().__init__(state_home)
                    self.results = [initial, sliced]

                def _git_status(self, _repo):
                    return ""

                def _generate_for_provider(self, **_kwargs):
                    result = self.results.pop(0)
                    return result, state_home / "fake.log"

            planner = FakePlanner()
            result = planner.generate(
                provider="codex",
                project_name="olchangi",
                source_repo=str(repo),
                requirement="Implement the feature.",
            )

            self.assertEqual(result["title"], "Feature")
            self.assertEqual(result["design_markdown"], "# Architecture")
            self.assertEqual(result["story_budget"]["status"], "PASS")
            self.assertEqual(result["story_budget"]["reslice_passes"], 1)
            self.assertEqual(len(result["stories"]), 2)
            self.assertTrue(
                all(
                    len(story["allowed_paths"]) <= LOCAL_STORY_HARD_MAX_PATHS
                    for story in result["stories"]
                )
            )


class GeminiLoginScriptTests(unittest.TestCase):
    def test_login_script_keeps_terminal_open_after_failure(self):
        script = build_gemini_login_script(
            "/home/user/.nvm/versions/node/v24/bin/gemini"
        )

        self.assertIn("/home/user/.nvm/versions/node/v24/bin", script)
        self.assertIn("command -v node", script)
        self.assertIn("Gemini CLI exited with code $status", script)
        self.assertIn("Press Enter to close this window", script)
        self.assertIn('exit "$status"', script)

class CodexCommandTests(unittest.TestCase):
    def test_global_flags_are_before_exec_subcommand(self):
        from pathlib import Path

        command = build_codex_exec_command(
            "/usr/local/bin/codex",
            Path("/tmp/schema.json"),
            Path("/tmp/output.json"),
            "Plan this repository",
        )

        exec_index = command.index("exec")
        approval_index = command.index("--ask-for-approval")
        sandbox_index = command.index("--sandbox")
        output_schema_index = command.index("--output-schema")

        self.assertLess(approval_index, exec_index)
        self.assertLess(sandbox_index, exec_index)
        self.assertGreater(output_schema_index, exec_index)
        self.assertEqual(command[approval_index + 1], "never")
        self.assertEqual(command[sandbox_index + 1], "read-only")


class CodexModelTests(unittest.TestCase):
    def test_selected_model_is_passed_to_exec(self):
        from pathlib import Path

        command = build_codex_exec_command(
            "/usr/local/bin/codex",
            Path("/tmp/schema.json"),
            Path("/tmp/output.json"),
            "Plan this repository",
            "gpt-5.6-sol",
        )

        model_index = command.index("--model")
        exec_index = command.index("exec")

        self.assertGreater(model_index, exec_index)
        self.assertEqual(command[model_index + 1], "gpt-5.6-sol")

    def test_default_model_omits_model_flag(self):
        from pathlib import Path

        command = build_codex_exec_command(
            "/usr/local/bin/codex",
            Path("/tmp/schema.json"),
            Path("/tmp/output.json"),
            "Plan this repository",
        )

        self.assertNotIn("--model", command)


class CodexReasoningEffortTests(unittest.TestCase):
    def test_selected_reasoning_effort_is_passed_as_global_config(self):
        from pathlib import Path

        command = build_codex_exec_command(
            "/usr/local/bin/codex",
            Path("/tmp/schema.json"),
            Path("/tmp/output.json"),
            "Plan this repository",
            "gpt-5.6-sol",
            "high",
        )

        config_index = command.index("--config")
        exec_index = command.index("exec")

        self.assertLess(config_index, exec_index)
        self.assertEqual(
            command[config_index + 1],
            'model_reasoning_effort="high"',
        )

    def test_default_reasoning_effort_omits_config_override(self):
        from pathlib import Path

        command = build_codex_exec_command(
            "/usr/local/bin/codex",
            Path("/tmp/schema.json"),
            Path("/tmp/output.json"),
            "Plan this repository",
            "gpt-5.6-sol",
        )

        self.assertNotIn("--config", command)


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
