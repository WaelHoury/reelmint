"""Batch publishing helpers: schedule, LLM metadata, credits description, YouTube scheduling."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.api.services.batch_service import (
    build_description,
    compute_publish_at,
    credit_lines,
    load_credit_assets,
    rfc3339,
)
from scene_planner import ScenePlanner, fallback_publish_metadata, normalize_tags

UTC = timezone.utc


# ── schedule ──────────────────────────────────────────────────


def test_schedule_interval_adds_k_intervals():
    now = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
    schedule = {"mode": "interval", "start_at": "2026-11-15T09:00:00Z", "interval_minutes": 240}
    assert compute_publish_at(schedule, 0, now) == datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
    assert compute_publish_at(schedule, 1, now) == datetime(2026, 11, 15, 13, 0, tzinfo=UTC)
    assert compute_publish_at(schedule, 3, now) == datetime(2026, 11, 15, 21, 0, tzinfo=UTC)


def test_schedule_slot_in_past_uses_now_plus_15_minutes():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    schedule = {"mode": "interval", "start_at": "2026-11-15T09:00:00Z", "interval_minutes": 60}
    assert compute_publish_at(schedule, 2, now) == now + timedelta(minutes=15)
    # A later slot that is still in the future is kept as-is
    far = compute_publish_at(schedule, 200, now)
    assert far == datetime(2026, 11, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=200 * 60)


def test_schedule_immediate_or_missing_returns_none():
    assert compute_publish_at(None, 0) is None
    assert compute_publish_at({"mode": "immediate"}, 3) is None


def test_rfc3339_format():
    assert rfc3339(datetime(2026, 11, 15, 9, 0, 30, 123, tzinfo=UTC)) == "2026-11-15T09:00:30Z"


# ── generate_publish_metadata ─────────────────────────────────


def _planner(response=None, error=None):
    planner = ScenePlanner.__new__(ScenePlanner)  # no LLM client
    planner._call_llm = MagicMock(side_effect=error, return_value=response)
    return planner


NARRATION = (
    "Is a hot dog a sandwich? The bread wraps the filling, just like a sub. "
    "But nobody orders a sandwich at a ballpark. Let's settle this."
)


def test_metadata_parses_llm_json_with_fences():
    raw = '```json\n{"title": "Is a Hot Dog a Sandwich?", "description": "The great debate.", ' \
          '"tags": ["hot dog", "#sandwich", "Hot Dog", "food debate"]}\n```'
    meta = _planner(raw).generate_publish_metadata(NARRATION, "hot dogs")
    assert meta["title"] == "Is a Hot Dog a Sandwich?"
    assert meta["description"] == "The great debate."
    assert meta["tags"] == ["hot dog", "sandwich", "food debate"]  # cleaned + de-duplicated


def test_metadata_caps_title_and_tags():
    raw = json.dumps({
        "title": "A very long title " * 20,
        "description": "Desc <b>bold</b>",
        "tags": [f"tag{i}" for i in range(40)],
    })
    meta = _planner(raw).generate_publish_metadata(NARRATION, None)
    assert len(meta["title"]) <= 100
    assert len(meta["tags"]) == 15
    assert "<" not in meta["description"] and ">" not in meta["description"]


def test_metadata_falls_back_on_llm_error():
    meta = _planner(error=RuntimeError("LLM down")).generate_publish_metadata(NARRATION, "Is a hot dog a sandwich?")
    assert meta["title"] == "Is a hot dog a sandwich?"
    assert meta["description"] == "Is a hot dog a sandwich? The bread wraps the filling, just like a sub."
    assert isinstance(meta["tags"], list) and "sandwich" in meta["tags"]


def test_metadata_falls_back_on_garbage():
    meta = _planner("Sorry, I can't do that.").generate_publish_metadata(NARRATION, "Hot dogs")
    assert meta == fallback_publish_metadata(NARRATION, "Hot dogs")


def test_metadata_fills_missing_fields_from_fallback():
    meta = _planner('{"title": "", "tags": []}').generate_publish_metadata(NARRATION, "Hot dogs")
    assert meta["title"] == "Hot dogs"
    assert meta["description"].startswith("Is a hot dog a sandwich?")


def test_normalize_tags_total_length_cap():
    tags = normalize_tags(["x" * 90] * 1 + [f"{'y' * 80}{i}" for i in range(10)])
    assert sum(len(t) for t in tags) <= 450


# ── credits → description ─────────────────────────────────────


ASSETS = [
    {"video_url": "https://www.pexels.com/video/1/", "author": "Ana", "provider": "Pexels"},
    {"video_url": "https://pixabay.com/videos/2/", "author": "Bo", "provider": "Pixabay"},
    {"video_url": "https://www.pexels.com/video/1/", "author": "Ana", "provider": "Pexels"},  # duplicate
]


def test_credit_lines_format_and_dedup():
    assert credit_lines(ASSETS) == [
        "Footage: Ana (Pexels) https://www.pexels.com/video/1/",
        "Footage: Bo (Pixabay) https://pixabay.com/videos/2/",
    ]


def test_description_includes_footer_and_credits():
    desc = build_description("Great video.", "Subscribe for more!", ASSETS)
    assert desc == (
        "Great video.\n\nSubscribe for more!\n\nCredits:\n"
        "Footage: Ana (Pexels) https://www.pexels.com/video/1/\n"
        "Footage: Bo (Pixabay) https://pixabay.com/videos/2/"
    )


def test_description_capped_at_5000_chars():
    many = [
        {"video_url": f"https://www.pexels.com/video/{i}/", "author": f"Author {i}", "provider": "Pexels"}
        for i in range(400)
    ]
    desc = build_description("Intro " * 200, "Footer", many)
    assert len(desc) <= 5000
    assert desc.startswith("Intro")
    assert "Footage: Author 0 (Pexels)" in desc
    assert "more" in desc.splitlines()[-1]  # "...and N more"


def test_description_head_alone_too_long_is_truncated():
    desc = build_description("a" * 6000, None, ASSETS)
    assert len(desc) == 5000


def test_load_credit_assets_from_json_and_txt(tmp_path):
    json_path = tmp_path / "video_credits.json"
    json_path.write_text(json.dumps({"assets": ASSETS[:2]}))
    assert load_credit_assets([tmp_path / "missing.json", json_path]) == ASSETS[:2]

    txt_path = tmp_path / "other_credits.txt"
    txt_path.write_text(
        "=====\nVIDEO CREDITS & ATTRIBUTIONS\n=====\n\nTotal assets used: 1\n\n"
        "\nPEXELS\n-----\n\nScene 1:\n  Author: Ana\n  License: Pexels License\n"
        "  URL: https://www.pexels.com/video/1/\n\n=====\nLICENSE INFORMATION\n"
    )
    assert load_credit_assets([txt_path]) == [
        {"author": "Ana", "provider": "Pexels", "video_url": "https://www.pexels.com/video/1/"}
    ]


# ── YouTube upload_video scheduling ───────────────────────────


def _run_upload(tmp_path, metadata):
    from app.social import youtube

    video = tmp_path / "v.mp4"
    video.write_bytes(b"0123456789")
    init = MagicMock(status_code=200, headers={"Location": "https://upload.example/session"})
    done = MagicMock(status_code=200)
    done.json.return_value = {"id": "abc123"}
    with patch.object(youtube.requests, "post", return_value=init) as post, \
            patch.object(youtube.requests, "put", return_value=done):
        result = youtube.upload_video("token", video, metadata)
    return post.call_args.kwargs["json"], result


def test_upload_with_publish_at_is_private_and_scheduled(tmp_path):
    body, result = _run_upload(tmp_path, {
        "title": "T", "privacy_status": "public", "publish_at": "2026-11-15T09:00:00Z",
    })
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-11-15T09:00:00Z"
    assert result["platform_post_url"] == "https://www.youtube.com/watch?v=abc123"


def test_upload_without_publish_at_keeps_privacy(tmp_path):
    body, _ = _run_upload(tmp_path, {"title": "T", "privacy_status": "public"})
    assert body["status"]["privacyStatus"] == "public"
    assert "publishAt" not in body["status"]
