"""Parsing/keyword-refinement tests for scene_planner (no live LLM needed)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("pydantic")

import scene_planner  # noqa: E402
from scene_planner import (  # noqa: E402
    ScenePlanner, VisualClip, extract_clips_list, extract_json_payload, parse_llm_json,
)


CLIPS = [
    {"keywords": ["ocean waves", "beach"], "visual_description": "Waves crash on a sandy beach"},
    {"keywords": ["coffee cup", "table"], "visual_description": "Steam rises from a coffee cup"},
    {"keywords": ["city street", "cars"], "visual_description": "Cars drive down a busy street"},
]


def _planner():
    # Bypass __init__ so no ollama client is created
    return ScenePlanner.__new__(ScenePlanner)


# --- extract_json_payload / parse_llm_json ---------------------------------

def test_plain_json_object():
    assert parse_llm_json('{"clips": []}') == {"clips": []}


def test_strips_json_fence():
    raw = 'Sure!\n```json\n{"a": 1}\n```\nHope that helps.'
    assert parse_llm_json(raw) == {"a": 1}


def test_strips_leading_and_trailing_prose():
    raw = 'Here is your plan: {"narration": "hi", "clips": [{"k": 1}]} Enjoy!'
    assert parse_llm_json(raw) == {"narration": "hi", "clips": [{"k": 1}]}


def test_top_level_array_with_prose():
    raw = 'Clips below:\n[{"a": 1}, {"b": 2}]\nDone.'
    assert parse_llm_json(raw) == [{"a": 1}, {"b": 2}]


def test_strips_think_block():
    raw = '<think>maybe {"x": 0}?</think>\n{"y": 1}'
    assert parse_llm_json(raw) == {"y": 1}


def test_no_json_raises_decode_error():
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("I cannot help with that.")


def test_extract_json_payload_none():
    assert extract_json_payload(None) == ''


# --- extract_clips_list ------------------------------------------------------

def test_extract_clips_from_object():
    assert extract_clips_list({"clips": CLIPS}) == CLIPS


def test_extract_clips_from_top_level_list():
    assert extract_clips_list(CLIPS) == CLIPS


@pytest.mark.parametrize("bad", [{"scenes": []}, {"clips": "nope"}, "str", 5, None, []])
def test_extract_clips_bad_shapes_raise_value_error(bad):
    with pytest.raises(ValueError):
        extract_clips_list(bad)


# --- _parse_clips_only / _parse_continuous_plan ------------------------------

def test_parse_clips_only_top_level_list_with_prose():
    raw = "Here you go:\n```json\n" + json.dumps(CLIPS) + "\n```"
    clips = _planner()._parse_clips_only(raw, expected_clips=3)
    assert [c.keywords for c in clips] == [["ocean waves", "beach"], ["coffee cup", "table"],
                                           ["city street", "cars"]]


def test_parse_clips_only_missing_clips_is_value_error():
    with pytest.raises(ValueError):
        _planner()._parse_clips_only('{"scenes": []}', expected_clips=3)


def test_parse_continuous_plan_with_leading_prose():
    narration = " ".join(["word"] * 30)
    raw = "Okay! " + json.dumps({"narration": narration, "clips": CLIPS})
    plan = _planner()._parse_continuous_plan(raw, target_duration=10, words_per_second=3.0)
    assert len(plan.clips) == 3
    assert plan.narration == narration


def test_parse_continuous_plan_missing_keys_is_value_error():
    with pytest.raises(ValueError):
        _planner()._parse_continuous_plan('{"clips": []}', target_duration=10)
    with pytest.raises(ValueError):
        _planner()._parse_continuous_plan('[1, 2, 3]', target_duration=10)


def test_generate_clips_from_script_retries_on_bad_shape():
    p = _planner()
    p.provider, p.model = "ollama", "test"
    good = json.dumps({"clips": CLIPS})
    p._call_llm = MagicMock(side_effect=['{"oops": true}', good])
    plan = p.generate_clips_from_script("A short script about the sea. " * 3,
                                        duration_minutes=10 / 60, max_retries=3)
    assert len(plan.clips) == 3
    assert p._call_llm.call_count == 2


# --- _refine_keywords --------------------------------------------------------

def test_refine_keywords_string_is_split_on_commas():
    clips = [{"keywords": "ocean waves, beach, sunset", "visual_description": "A beach"}]
    out = ScenePlanner._refine_keywords(clips)
    assert out[0]["keywords"] == ["ocean waves", "beach", "sunset"]


def test_refine_keywords_skips_stopwords_from_description():
    clips = [{"keywords": ["process", "energy"],
              "visual_description": "Close shot of her hands for the bread dough"}]
    out = ScenePlanner._refine_keywords(clips)
    kws = out[0]["keywords"]
    assert "her" not in kws and "for" not in kws and "the" not in kws
    assert kws == ["hands", "bread", "dough"]
    VisualClip(**out[0])  # must validate


def test_refine_keywords_keeps_original_if_too_few_remain():
    clips = [{"keywords": ["process", "energy", "system"],
              "visual_description": "Her view of it"}]
    out = ScenePlanner._refine_keywords(clips)
    assert out[0]["keywords"] == ["process", "energy", "system"]
    VisualClip(**out[0])


def test_refine_keywords_normal_case_unchanged():
    clips = [dict(c) for c in CLIPS]
    out = ScenePlanner._refine_keywords(clips)
    assert [c["keywords"] for c in out] == [c["keywords"] for c in CLIPS]


# --- Ollama host -------------------------------------------------------------

def test_ollama_client_uses_base_url():
    fake = MagicMock()
    fake.Client.return_value.chat.return_value = {"message": {"content": "ok"}}
    with patch.dict(sys.modules, {"ollama": fake}):
        p = ScenePlanner(provider="ollama", model="m", base_url="http://host.docker.internal:11434")
        assert p._call_ollama([{"role": "user", "content": "hi"}]) == "ok"
    fake.Client.assert_called_once_with(host="http://host.docker.internal:11434")
    fake.chat.assert_not_called()


def test_clips_wrapped_under_another_key_are_found():
    from scene_planner import extract_clips_list
    clips = extract_clips_list({"visual_clips": [{"keywords": ["a", "b"], "visual_description": "x"}]})
    assert clips[0]["keywords"] == ["a", "b"]


def test_ollama_uses_json_mode_except_for_narration(monkeypatch):
    from scene_planner import ScenePlanner
    p = ScenePlanner(provider="ollama", model="m", base_url="http://localhost:11434")
    calls = []

    class FakeClient:
        def chat(self, **kw):
            calls.append(kw.get("format"))
            return {"message": {"content": "{}"}}

    p._ollama_client = FakeClient()
    p._call_llm([{"role": "user", "content": "x"}], stage="clips")
    p._call_llm([{"role": "user", "content": "x"}], stage="narration")
    assert calls == ["json", None]
