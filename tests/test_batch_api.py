"""Batch API: schema validation and the /api/batches routes."""

import pytest
from pydantic import ValidationError

from app.api.schemas.batch import BatchPublish, BatchSchedule, CreateBatchRequest


def _payload(**overrides):
    data = {
        "name": "Monday shorts",
        "items": [
            {"input_type": "prompt", "text": "Is a hot dog a sandwich?"},
            {"input_type": "script", "text": "A full script. With words.", "title": "Script one"},
        ],
        "settings": {"orientation": "portrait", "duration_seconds": 45},
        "publish": {"enabled": False},
    }
    data.update(overrides)
    return data


# ── schema validation ─────────────────────────────────────────


def test_schema_accepts_valid_payload():
    req = CreateBatchRequest(**_payload())
    assert len(req.items) == 2 and req.start is False
    assert req.publish.schedule is None


@pytest.mark.parametrize("count", [0, 101])
def test_schema_item_count_limits(count):
    items = [{"input_type": "prompt", "text": f"topic {i}"} for i in range(count)]
    with pytest.raises(ValidationError):
        CreateBatchRequest(**_payload(items=items))


def test_schema_100_items_ok():
    items = [{"input_type": "prompt", "text": f"topic {i}"} for i in range(100)]
    assert len(CreateBatchRequest(**_payload(items=items)).items) == 100


@pytest.mark.parametrize("item", [
    {"input_type": "prompt", "text": "   "},
    {"input_type": "prompt", "text": "x" * 501},
    {"input_type": "script", "text": "x" * 20001},
    {"input_type": "video", "text": "hello"},
    {"input_type": "prompt", "text": "ok", "title": "a/b"},
])
def test_schema_rejects_bad_items(item):
    with pytest.raises(ValidationError):
        CreateBatchRequest(**_payload(items=[item]))


@pytest.mark.parametrize("duration", [14, 601])
def test_schema_duration_bounds(duration):
    with pytest.raises(ValidationError):
        CreateBatchRequest(**_payload(settings={"orientation": "portrait", "duration_seconds": duration}))


def test_interval_schedule_requires_start_at_and_min_interval():
    with pytest.raises(ValidationError, match="start time"):
        BatchSchedule(mode="interval", interval_minutes=60)
    with pytest.raises(ValidationError, match="at least 15 minutes"):
        BatchSchedule(mode="interval", start_at="2026-11-15T09:00:00Z", interval_minutes=10)
    sched = BatchSchedule(mode="interval", start_at="2026-11-15T10:00:00+01:00", interval_minutes=15)
    assert sched.start_at == "2026-11-15T09:00:00Z"  # normalized to UTC
    assert BatchSchedule(mode="immediate").start_at is None


def test_publish_requires_account_when_enabled():
    with pytest.raises(ValidationError, match="YouTube account"):
        BatchPublish(enabled=True)
    assert BatchPublish(enabled=True, account_id=3).account_id == 3
    assert BatchPublish(enabled=False).account_id is None


# ── routes ────────────────────────────────────────────────────


@pytest.fixture
def client(batch_env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.api.routes.batches import router
    from app.db.base import get_db
    from app.db.models import User

    app = FastAPI()
    app.include_router(router, prefix="/api")

    def _db():
        db = batch_env.Session()
        try:
            yield db
        finally:
            db.close()

    def _user():
        db = batch_env.Session()
        user = db.get(User, batch_env.user_id)
        db.close()
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _account(batch_env, status="active"):
    from app.db.models import SocialAccount
    db = batch_env.Session()
    acc = SocialAccount(user_id=batch_env.user_id, provider="youtube", provider_user_id=f"c-{status}",
                        display_name="My Channel", access_token_enc="x", status=status)
    db.add(acc)
    db.commit()
    acc_id = acc.id
    db.close()
    return acc_id


def test_create_get_list(client):
    r = client.post("/api/batches", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["total"] == 2
    assert body["counts"] == {"pending": 2, "in_progress": 0, "done": 0, "published": 0,
                              "failed": 0, "cancelled": 0}
    assert body["publish_enabled"] is False
    assert body["settings"]["duration_seconds"] == 45
    item = body["items"][1]
    assert set(item) == {"id", "position", "input_type", "title", "text_preview", "status",
                         "progress_percent", "current_step", "video_id", "youtube_url",
                         "scheduled_publish_at", "error_message", "attempts", "next_attempt_at",
                         "started_at", "finished_at"}
    assert item["title"] == "Script one" and item["position"] == 1
    assert body["created_at"].endswith("Z")

    second = client.post("/api/batches", json=_payload(name="Second")).json()
    listing = client.get("/api/batches").json()
    assert [b["id"] for b in listing["batches"]] == [second["id"], body["id"]]  # newest first
    assert "items" not in listing["batches"][0]

    assert client.get(f"/api/batches/{body['id']}").json()["id"] == body["id"]
    assert client.get("/api/batches/9999").status_code == 404


def test_validation_errors_are_400_with_sentence(client):
    r = client.post("/api/batches", json=_payload(items=[{"input_type": "prompt", "text": "x" * 600}]))
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Video 1: Topic is too long")

    r = client.post("/api/batches", json=_payload(publish={"enabled": True}))
    assert r.status_code == 400
    assert r.json()["detail"] == "Choose a YouTube account to publish to"


def test_publish_account_must_be_active(client, batch_env):
    expired = _account(batch_env, status="expired")
    r = client.post("/api/batches", json=_payload(publish={"enabled": True, "account_id": expired}))
    assert r.status_code == 400 and "reconnected" in r.json()["detail"]

    r = client.post("/api/batches", json=_payload(publish={"enabled": True, "account_id": 12345}))
    assert r.status_code == 400 and "isn't connected" in r.json()["detail"]

    active = _account(batch_env)
    r = client.post("/api/batches", json=_payload(publish={
        "enabled": True, "account_id": active,
        "schedule": {"mode": "interval", "start_at": "2026-11-15T09:00:00Z", "interval_minutes": 60},
    }))
    assert r.status_code == 201, r.text
    assert r.json()["publish_enabled"] is True
    assert r.json()["publish"]["schedule"]["start_at"] == "2026-11-15T09:00:00Z"


def test_start_pause_cancel_flow(client, batch_env):
    from rq import Queue
    batch = client.post("/api/batches", json=_payload(start=True)).json()
    assert batch["status"] == "running"
    q = Queue("video_generation", connection=batch_env.redis)
    assert q.count == 1

    # Starting again does not queue a second runner
    assert client.post(f"/api/batches/{batch['id']}/start").json()["status"] == "running"
    assert q.count == 1

    assert client.post(f"/api/batches/{batch['id']}/pause").json()["status"] == "paused"
    cancelled = client.post(f"/api/batches/{batch['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["counts"]["cancelled"] == 2

    r = client.post(f"/api/batches/{batch['id']}/items", json={"items": [{"input_type": "prompt", "text": "more"}]})
    assert r.status_code == 400

    # Retry a cancelled item reopens the batch as paused, then it can be started
    item_id = cancelled["items"][0]["id"]
    retried = client.post(f"/api/batches/{batch['id']}/items/{item_id}/retry").json()
    assert retried["status"] == "paused"
    assert retried["items"][0]["status"] == "pending"
    assert client.post(f"/api/batches/{batch['id']}/start").json()["status"] == "running"


def test_pause_requires_running(client):
    batch = client.post("/api/batches", json=_payload()).json()
    r = client.post(f"/api/batches/{batch['id']}/pause")
    assert r.status_code == 400 and r.json()["detail"]


def test_append_items_and_reopen_completed(client, batch_env):
    from app.db.models import Batch
    batch = client.post("/api/batches", json=_payload()).json()
    db = batch_env.Session()
    b = db.get(Batch, batch["id"])
    b.status = "completed"
    for item in b.items:
        item.status = "done"
    db.commit()
    db.close()

    r = client.post(f"/api/batches/{batch['id']}/items",
                    json={"items": [{"input_type": "prompt", "text": "Why is the sky blue?"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paused"
    assert body["total"] == 3
    assert body["items"][-1]["position"] == 2 and body["items"][-1]["status"] == "pending"

    too_many = [{"input_type": "prompt", "text": f"t{i}"} for i in range(98)]
    r = client.post(f"/api/batches/{batch['id']}/items", json={"items": too_many})
    assert r.status_code == 400 and "at most 100" in r.json()["detail"]


def test_delete_item_and_batch_rules(client, batch_env):
    from app.db.models import BatchItem
    batch = client.post("/api/batches", json=_payload()).json()
    first, second = batch["items"]

    db = batch_env.Session()
    db.get(BatchItem, second["id"]).status = "rendering"
    db.commit()
    db.close()

    assert client.delete(f"/api/batches/{batch['id']}/items/{second['id']}").status_code == 400
    assert client.delete(f"/api/batches/{batch['id']}").status_code == 409

    assert client.delete(f"/api/batches/{batch['id']}/items/{first['id']}").status_code == 204
    assert client.delete(f"/api/batches/{batch['id']}/items/{first['id']}").status_code == 404

    db = batch_env.Session()
    db.get(BatchItem, second["id"]).status = "done"
    db.commit()
    db.close()
    assert client.delete(f"/api/batches/{batch['id']}").status_code == 204
    assert client.get(f"/api/batches/{batch['id']}").status_code == 404
    db = batch_env.Session()
    assert db.query(BatchItem).count() == 0
    db.close()


def test_youtube_quota_route_not_shadowed(client):
    r = client.get("/api/batches/youtube-quota")
    assert r.status_code == 200
    body = r.json()
    assert body["uploads_per_day_estimate"] is None  # unset unless YOUTUBE_DAILY_UPLOAD_LIMIT
    assert "compliance audit" in body["note"]


def test_youtube_quota_limit_from_env(client, monkeypatch):
    monkeypatch.setenv("YOUTUBE_DAILY_UPLOAD_LIMIT", "10")
    assert client.get("/api/batches/youtube-quota").json()["uploads_per_day_estimate"] == 10
