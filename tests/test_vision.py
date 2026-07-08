import json
from pathlib import Path

import pytest

from app.vision.cli_agent import CliAgentProvider, _unwrap_agent_output
from app.vision.provider import VisionError, parse_recipe
from app.vision.registry import build_provider

VALID_RECIPE = {
    "look_description": "Warm teal-orange with lifted blacks.",
    "temperature": 0.3,
    "tint": 0.0,
    "lift": {"r": 0.02, "g": 0.01, "b": 0.05},
    "gamma": {"r": 1.0, "g": 1.0, "b": 0.95},
    "gain": {"r": 1.1, "g": 1.0, "b": 0.9},
    "contrast": 1.2,
    "saturation": 0.9,
    "hue_saturation": [1.0, 1.1, 0.7, 1.0, 1.2, 1.0],
    "tone_curve": [{"x": 0.5, "y": 0.55}],
    "split_tone": {
        "shadow": {"r": -0.03, "g": 0.0, "b": 0.06},
        "highlight": {"r": 0.05, "g": 0.02, "b": -0.02},
        "amount": 0.5,
    },
}


class TestParseRecipe:
    def test_plain_json(self):
        recipe = parse_recipe(json.dumps(VALID_RECIPE))
        assert recipe.temperature == 0.3
        assert recipe.hue_saturation[2] == 0.7

    def test_markdown_fenced_json(self):
        text = "Here you go:\n```json\n" + json.dumps(VALID_RECIPE) + "\n```\nEnjoy!"
        assert parse_recipe(text).contrast == 1.2

    def test_json_with_chatter(self):
        text = "Sure! " + json.dumps(VALID_RECIPE) + " Let me know."
        assert parse_recipe(text).saturation == 0.9

    def test_garbage_raises(self):
        with pytest.raises(VisionError):
            parse_recipe("I cannot analyze these images.")

    def test_schema_violation_raises(self):
        bad = dict(VALID_RECIPE, hue_saturation=[1.0])
        with pytest.raises(VisionError, match="schema"):
            parse_recipe(json.dumps(bad))


class TestUnwrap:
    def test_claude_code_envelope(self):
        envelope = json.dumps({"type": "result", "result": json.dumps(VALID_RECIPE)})
        assert parse_recipe(_unwrap_agent_output(envelope)).temperature == 0.3

    def test_raw_output_passthrough(self):
        assert _unwrap_agent_output("  hello  ") == "hello"


class TestCliProvider:
    def test_missing_binary_gives_clear_error(self, tmp_path):
        provider = CliAgentProvider("definitely-not-a-real-binary-xyz -p {prompt}")
        with pytest.raises(VisionError, match="not found"):
            provider.extract_dna(tmp_path / "a.png", tmp_path / "b.png")

    def test_repair_retry_on_bad_first_reply(self, tmp_path, monkeypatch):
        provider = CliAgentProvider("fake {prompt}")
        replies = iter(["not json at all", json.dumps(VALID_RECIPE)])
        monkeypatch.setattr(provider, "_run", lambda prompt: next(replies))
        recipe = provider.extract_dna(tmp_path / "a.png", tmp_path / "b.png")
        assert recipe.contrast == 1.2

    def test_template_without_prompt_placeholder(self, tmp_path):
        provider = CliAgentProvider("echo hello")
        with pytest.raises(VisionError, match="lacks"):
            provider.extract_dna(tmp_path / "a.png", tmp_path / "b.png")


class TestRegistry:
    def test_none_provider(self):
        assert build_provider({"vision": {"provider": "none"}}) is None

    def test_claude_default(self):
        p = build_provider({})
        assert p is not None and p.name == "claude"

    def test_generic_cli(self):
        p = build_provider({"vision": {"provider": "cli", "command_template": "puppy {prompt}"}})
        assert p is not None

    def test_generic_cli_bad_template(self):
        assert build_provider({"vision": {"provider": "cli", "command_template": "puppy"}}) is None
