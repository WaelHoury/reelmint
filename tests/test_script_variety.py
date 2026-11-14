"""Scripts should not all come out with the same shape."""

import random

from script_variety import (
    ENDINGS, HOOKS, STRUCTURES, has_banned_opener, pick_angle, variety_block,
)
from scene_planner import ScenePlanner


def test_angles_vary_across_videos():
    rng = random.Random(7)
    angles = {pick_angle("viral", rng) for _ in range(40)}
    assert len(angles) > 20  # many distinct combinations, not one template
    assert len(HOOKS) * len(STRUCTURES) * len(ENDINGS) >= 500


def test_creative_style_gets_varied_casual_openers():
    from script_variety import CREATIVE_HOOKS
    rng = random.Random(1)
    hooks = {pick_angle("creative", rng).hook for _ in range(30)}
    assert hooks <= set(CREATIVE_HOOKS + HOOKS) and len(hooks) > 5


def test_no_prompt_teaches_a_fixed_opener():
    import scene_planner
    src = open(scene_planner.__file__).read().lower()
    assert "okay but like" not in src


def test_variety_block_includes_channel_voice(monkeypatch):
    angle = pick_angle("viral", random.Random(3))
    monkeypatch.setenv("CHANNEL_VOICE", "Dry humour, one oddly specific detail per video.")
    block = variety_block(angle)
    assert "CHANNEL VOICE" in block and "oddly specific" in block
    assert angle.structure in block and angle.ending in block


def test_banned_openers_detected():
    assert has_banned_opener("Did you know that octopuses have three hearts?")
    assert has_banned_opener("  Ever wondered why cats purr?")
    assert has_banned_opener("Let's dive into the ocean.")
    assert has_banned_opener("Okay but like... is cereal soup?")
    assert has_banned_opener("Okay, so flamingos stand on one leg.")
    assert has_banned_opener("...and the bun is one piece of bread.")
    assert has_banned_opener("And the bun is one piece of bread.")
    assert not has_banned_opener("Octopuses have three hearts, and one stops when they swim.")


def test_narration_retries_cliche_opening(monkeypatch):
    planner = ScenePlanner(provider="ollama", model="m", base_url="http://localhost:11434")
    replies = iter([
        "Did you know " + "word " * 60,
        "Octopuses have three hearts " + "word " * 60,
    ])
    seen_prompts = []

    def fake_llm(messages, temperature=0.7, stage="x"):
        seen_prompts.append(messages[0]["content"])
        return next(replies)

    monkeypatch.setattr(planner, "_call_llm", fake_llm)
    out = planner.generate_narration("octopus hearts", 0.3, 3.0)
    assert out.startswith("Octopuses")
    assert len(seen_prompts) == 2
    assert "THIS VIDEO'S ANGLE" in seen_prompts[0]


def test_styles_do_not_fix_openings_or_endings():
    for name, style in ScenePlanner.NARRATION_STYLES.items():
        rules = style["rules"].lower()
        assert "open with" not in rules and "open by" not in rules, name
        assert "end with" not in rules, name
