"""Which model answers an assistant style: the owner maps, the student never names one."""

import unittest

from learnova.assistant import routing
from learnova.assistant.presets import PRESET_NAMES

BASE = {"GROQ_TUTOR_MODEL": "openai/gpt-oss-20b", "GROQ_ANALYSIS_MODEL": "openai/gpt-oss-120b",
        "ASSISTANT_MODEL": "openai/gpt-oss-20b", "ASSISTANT_DEEP_MODEL": "openai/gpt-oss-120b"}


class AssistantRoutingTests(unittest.TestCase):
    def test_an_unmapped_style_uses_the_global_models(self):
        normal = routing.assistant_model_for("general", False, BASE)
        deep = routing.assistant_model_for("general", True, BASE)
        self.assertEqual((normal.model, normal.setting, normal.mapped), ("openai/gpt-oss-20b", "ASSISTANT_MODEL", False))
        self.assertEqual((deep.model, deep.setting, deep.mapped), ("openai/gpt-oss-120b", "ASSISTANT_DEEP_MODEL", False))

    def test_a_mapped_style_wins_for_its_depth_only(self):
        config = {**BASE, "ASSISTANT_MODEL_RESEARCH": "anthropic:claude-x"}
        mapped = routing.assistant_model_for("research", False, config)
        self.assertEqual((mapped.model, mapped.setting, mapped.mapped), ("anthropic:claude-x", "ASSISTANT_MODEL_RESEARCH", True))
        deep = routing.assistant_model_for("research", True, config)
        self.assertEqual(deep.model, "openai/gpt-oss-120b", "deep is a separate mapping")

    def test_a_deep_mapping_is_separate(self):
        config = {**BASE, "ASSISTANT_DEEP_MODEL_STUDY_COACH": "openai:gpt-x"}
        self.assertEqual(routing.assistant_model_for("study_coach", True, config).model, "openai:gpt-x")
        self.assertEqual(routing.assistant_model_for("study_coach", False, config).model, "openai/gpt-oss-20b")

    def test_an_unknown_style_is_the_default_style(self):
        config = {**BASE, "ASSISTANT_MODEL_GENERAL": "gemini:gemini-x"}
        self.assertEqual(routing.assistant_model_for("hacker", False, config).model, "gemini:gemini-x")
        self.assertEqual(routing.assistant_model_for(None, False, config).model, "gemini:gemini-x")

    def test_blank_settings_fall_through(self):
        config = {**BASE, "ASSISTANT_MODEL_EXPLAIN": "   ", "ASSISTANT_MODEL": ""}
        self.assertEqual(routing.assistant_model_for("explain", False, config).setting, "GROQ_TUTOR_MODEL")

    def test_every_style_has_two_setting_names_and_they_are_well_formed(self):
        names = routing.preset_model_settings()
        self.assertEqual(len(names), 2 * len(PRESET_NAMES))
        for name in names:
            self.assertRegex(name, r"^ASSISTANT_(DEEP_)?MODEL_[A-Z_]+$")
        self.assertIn("ASSISTANT_MODEL_RESEARCH", names)
        self.assertIn("ASSISTANT_DEEP_MODEL_STUDY_COACH", names)


if __name__ == "__main__":
    unittest.main()
