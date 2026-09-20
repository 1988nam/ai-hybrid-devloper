import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlannerUiTests(unittest.TestCase):
    def test_import_actions_are_visually_button_like_controls(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("MD 파일 불러오기", html)
        self.assertIn("JSON 파일 불러오기", html)
        self.assertIn('class="file-button"', html)
        self.assertIn(".file-button {", css)
        self.assertIn("border:", css)

    def test_codex_model_selector_is_present_and_sent_to_backend(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="plannerModelSelect"', html)
        self.assertIn('id="plannerCustomModel"', html)
        self.assertIn('id="plannerEffortSelect"', html)
        self.assertIn('model: state.plannerProvider === "codex"', js)
        self.assertIn("reasoning_effort:", js)
        self.assertIn("aiHybridCodexModel", js)
        self.assertIn("aiHybridCodexReasoningEffort", js)


if __name__ == "__main__":
    unittest.main()
