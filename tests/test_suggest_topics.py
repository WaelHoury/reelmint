"""AI topic brainstorming: parsing, dedup against existing topics, top-up call, routes, theme."""

import json

import pytest

import scene_planner
from scene_planner import ScenePlanner, clean_topic, dedupe_topics, normalize_topic, parse_topics_response
from tests.test_batch_api import client  # noqa: F401


def _planner(responses):
    """A ScenePlanner whose LLM returns ``responses`` in order (and records the prompts)."""
    planner = ScenePlanner.__new__(ScenePlanner)
    planner.prompts = []

    def fake_call(messages, temperature=0.7, stage="unknown"):
        planner.prompts.append(messages)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    planner._call_llm = fake_call
    return planner


def test_parse_topics_response_shapes():
    assert parse_topics_response('```json\n{"topics": ["A b c d e f?", " x "]}\n```') == ["A b c d e f?", "x"]
    assert parse_topics_response('[{"topic": "One topic here"}, "Two topic here", 3]') == \
        ["One topic here", "Two topic here"]
    with pytest.raises(ValueError):
        parse_topics_response('{"nope": 1}')


def test_clean_topic_rules():
    assert clean_topic('1. "Why do cats knead blankets?"') == "Why do cats knead blankets?"
    assert clean_topic("- Octopuses taste with their arms #science") == "Octopuses taste with their arms"
    assert clean_topic("x" * 91) is None                       # over 90 characters
    assert clean_topic("Did you know honey never spoils?") is None  # banned cliche
    assert clean_topic("The mind-blowing truth about sand") is None
    assert clean_topic("Short") is None
    assert normalize_topic("Why Do Cats Purr?!") == "why do cats purr"


def test_dedupe_against_avoid_and_itself():
    avoid = ["Why do octopuses have three hearts?", "A full script about bread. " * 50]
    topics = dedupe_topics(
        [
            "Why do octopuses have 3 hearts?",          # near-duplicate of an existing topic
            "WHY DO OCTOPUSES HAVE THREE HEARTS",        # same, different case/punctuation
            "Flamingos are pink because of shrimp",
            "flamingos are pink because of shrimp!",     # duplicate within the batch
            "Bread rises because yeast exhales",
        ],
        avoid,
    )
    assert topics == ["Flamingos are pink because of shrimp", "Bread rises because yeast exhales"]


def test_suggest_topics_tops_up_once():
    planner = _planner([
        json.dumps({"topics": ["Why do owls turn their heads so far?", "Why do owls turn their heads so far",
                               "Did you know bees dance?", "Sloths can hold their breath longer than dolphins"]}),
        json.dumps({"topics": ["Crows remember human faces for years", "Ants farm aphids like cattle",
                               "Penguins propose with pebbles"]}),
    ])
    topics = planner.suggest_topics("animal facts", 4, avoid=["Penguins propose with pebbles"])
    assert topics == [
        "Why do owls turn their heads so far?",
        "Sloths can hold their breath longer than dolphins",
        "Crows remember human faces for years",
        "Ants farm aphids like cattle",
    ]
    assert len(planner.prompts) == 2
    second_user = planner.prompts[1][1]["content"]
    assert "Why do owls turn their heads so far?" in second_user  # accepted ones are avoided too
    assert "Penguins propose with pebbles" in second_user
    assert "Write 6 topics" in planner.prompts[0][1]["content"]  # 4 + a few spare for dedup losses


def test_suggest_topics_no_top_up_when_enough_and_errors():
    planner = _planner([json.dumps({"topics": ["Crows remember human faces for years"]})])
    assert planner.suggest_topics("birds", 1) == ["Crows remember human faces for years"]
    assert len(planner.prompts) == 1

    # A failing top-up keeps what the first call produced
    planner = _planner([json.dumps({"topics": ["Crows remember human faces for years"]}), RuntimeError("down")])
    assert planner.suggest_topics("birds", 3) == ["Crows remember human faces for years"]

    with pytest.raises(RuntimeError):
        _planner([RuntimeError("down")]).suggest_topics("birds", 3)
    with pytest.raises(ValueError):
        _planner([json.dumps({"topics": []}), json.dumps({"topics": []})]).suggest_topics("birds", 3)


# ── routes ────────────────────────────────────────────────────


class FakeTopicPlanner:
    def __init__(self, topics=None, error=None):
        self.topics = topics or []
        self.error = error
        self.calls = []

    def suggest_topics(self, niche, count, avoid=None, style=None):
        self.calls.append({"niche": niche, "count": count, "avoid": list(avoid or []), "style": style})
        if self.error:
            raise self.error
        return self.topics[:count]


@pytest.fixture
def fake_planner(monkeypatch):
    from app.api.services import batch_service
    holder = {"planner": FakeTopicPlanner(["Topic number one here", "Topic number two here",
                                           "Topic number three here"])}
    monkeypatch.setattr(batch_service, "_topic_planner", lambda style=None: holder["planner"])
    return holder


def test_suggest_topics_route(client, fake_planner, batch_env):
    from app.db.models import Video
    db = batch_env.Session()
    db.add(Video(user_id=batch_env.user_id, title="Old video title", input_type="prompt",
                 duration_seconds=30, orientation="portrait", output_filename="x.mp4"))
    db.commit()
    db.close()
    client.post("/api/batches", json={"name": "b", "items": [
        {"input_type": "prompt", "text": "Queued topic text"},
        {"input_type": "script", "text": "Long script body", "title": "Script title"},
    ]})

    r = client.post("/api/batches/suggest-topics", json={"niche": "ocean life", "count": 2, "style": "viral"})
    assert r.status_code == 200, r.text
    assert r.json() == {"topics": ["Topic number one here", "Topic number two here"]}
    call = fake_planner["planner"].calls[0]
    assert call["niche"] == "ocean life" and call["count"] == 2 and call["style"] == "viral"
    assert set(call["avoid"]) == {"Old video title", "Queued topic text", "Script title"}


@pytest.mark.parametrize("body", [
    {"niche": "ab", "count": 3},
    {"niche": "x" * 201, "count": 3},
    {"niche": "ocean life", "count": 0},
    {"niche": "ocean life", "count": 51},
    {"count": 3},
])
def test_suggest_topics_route_validation(client, fake_planner, body):
    r = client.post("/api/batches/suggest-topics", json=body)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_suggest_topics_route_llm_failure(client, fake_planner):
    fake_planner["planner"] = FakeTopicPlanner(error=ConnectionError("ollama is not running"))
    r = client.post("/api/batches/suggest-topics", json={"niche": "ocean life", "count": 3})
    assert r.status_code == 502
    assert "ollama is not running" in r.json()["detail"]

    fake_planner["planner"] = FakeTopicPlanner(topics=[])
    assert client.post("/api/batches/suggest-topics", json={"niche": "ocean life", "count": 3}).status_code == 502


def test_create_batch_from_theme_hands_off(client, fake_planner):
    r = client.post("/api/batches", json={
        "name": "Overnight", "items": [], "hands_off": True,
        "theme": {"niche": "ocean life", "count": 3},
        "settings": {"orientation": "portrait", "duration_seconds": 45, "narration_style": "viral"},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["hands_off"] is True and body["total"] == 3
    assert [i["text_preview"] for i in body["items"]] == [
        "Topic number one here", "Topic number two here", "Topic number three here"]
    assert all(i["input_type"] == "prompt" and i["next_attempt_at"] is None for i in body["items"])
    assert fake_planner["planner"].calls[0]["style"] == "viral"
    listing = client.get("/api/batches").json()["batches"]
    assert listing[0]["hands_off"] is True


def test_create_batch_items_take_precedence_over_theme(client, fake_planner):
    r = client.post("/api/batches", json={
        "name": "b", "items": [{"input_type": "prompt", "text": "My own topic"}],
        "theme": {"niche": "ocean life", "count": 3},
    })
    assert r.status_code == 201
    assert r.json()["total"] == 1 and r.json()["hands_off"] is False
    assert fake_planner["planner"].calls == []


def test_create_batch_needs_items_or_theme(client, fake_planner):
    r = client.post("/api/batches", json={"name": "b", "items": []})
    assert r.status_code == 400 and r.json()["detail"] == "Add at least one video"
    r = client.post("/api/batches", json={"name": "b"})
    assert r.status_code == 400 and r.json()["detail"] == "Add at least one video"
    r = client.post("/api/batches", json={"name": "b", "theme": {"niche": "ab", "count": 3}})
    assert r.status_code == 400


def test_create_batch_theme_llm_failure(client, fake_planner):
    fake_planner["planner"] = FakeTopicPlanner(error=RuntimeError("boom"))
    r = client.post("/api/batches", json={"name": "b", "theme": {"niche": "ocean life", "count": 3}})
    assert r.status_code == 502
    assert client.get("/api/batches").json()["batches"] == []  # nothing half-created
