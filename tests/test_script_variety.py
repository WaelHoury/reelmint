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


def test_narration_is_trimmed_to_fit_the_video_length():
    from scene_planner import fit_to_word_budget
    text = "One two three. Four five six seven. Eight nine ten eleven twelve."
    assert fit_to_word_budget(text, 8) == "One two three. Four five six seven."
    assert fit_to_word_budget(text, 100) == text
    assert fit_to_word_budget("A very long first sentence here.", 2) == "A very long first sentence here."


def test_overlong_narration_gets_trimmed(monkeypatch):
    planner = ScenePlanner(provider="ollama", model="m", base_url="http://localhost:11434")
    long_script = " ".join(f"Sentence number {i} is here." for i in range(40))  # 200 words
    monkeypatch.setattr(planner, "_call_llm", lambda *a, **k: long_script)
    out = planner.generate_narration("topic", 0.5, 2.5)  # 30 s * 2.5 = 75 words
    assert len(out.split()) <= int(75 * 1.1)
    assert out.endswith(".")


def test_short_narration_is_retried_with_feedback_and_best_kept(monkeypatch):
    planner = ScenePlanner(provider="ollama", model="m", base_url="http://localhost:11434")
    drafts = iter([
        " ".join(["Short draft sentence here."] * 10),   # 40 words, target 75
        " ".join(["Longer draft sentence now ok."] * 11),  # 55 words, still under 60
        " ".join(["Tiny draft."] * 20),                   # 40 words
    ])
    prompts = []

    def fake_llm(messages, temperature=0.7, stage="x"):
        prompts.append(messages[1]["content"])
        return next(drafts)

    monkeypatch.setattr(planner, "_call_llm", fake_llm)
    out = planner.generate_narration("topic", 0.5, 2.5)   # 30 s * 2.5 = 75 words
    assert len(prompts) == 3
    assert "only 40 words" in prompts[1]
    assert len(out.split()) == 55   # the longest draft is kept rather than failing
