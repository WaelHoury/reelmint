"""Hands-off autopilot: automatic retries with backoff, runner wake-ups, YouTube quota waits."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from rq import Queue
from rq.registry import ScheduledJobRegistry

from app.api.services import batch_service, publish_worker_task as pwt
from app.db.models import Batch, BatchItem, PublishJob, SocialAccount, Video
from app.social import youtube
from tests.test_batch_runner import _batch_status, _drain, _items, _make_batch, pipeline  # noqa: F401


def _set_hands_off(env, batch_id, value=True):
    db = env.Session()
    db.get(Batch, batch_id).hands_off = value
    db.commit()
    db.close()


def _scheduled_runner(env):
    """(job, aware UTC time) of the scheduled runner wake-ups."""
    q = Queue("video_generation", connection=env.redis)
    reg = ScheduledJobRegistry(queue=q)
    out = []
    for job_id in reg.get_job_ids():
        job = q.fetch_job(job_id)
        when = reg.get_scheduled_time(job)
        out.append((job, when if when.tzinfo else when.replace(tzinfo=timezone.utc)))
    return out


def _make_due(env, batch_id):
    db = env.Session()
    for item in db.query(BatchItem).filter_by(batch_id=batch_id):
        if item.next_attempt_at:
            item.next_attempt_at = batch_service.utcnow() - timedelta(seconds=1)
    db.commit()
    db.close()


def _approx(dt, expected, slack=timedelta(seconds=30)):
    return abs(dt - expected) <= slack


def test_hands_off_retries_with_backoff_then_fails(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["FAIL always"])
    _set_hands_off(batch_env, batch_id)

    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "pending" and item.attempts == 1
    assert _approx(item.next_attempt_at, batch_service.utcnow() + timedelta(minutes=2))
    assert item.error_message.startswith("Attempt 1 of 3 failed")

    # The chained runner finds nothing ready and schedules a wake-up for the retry
    _drain(batch_env, batch_id)
    wakes = _scheduled_runner(batch_env)
    assert len(wakes) == 1 and wakes[0][0].func_name == batch_service.RUNNER_JOB
    assert _approx(wakes[0][1], item.next_attempt_at.replace(tzinfo=timezone.utc))
    assert _batch_status(batch_env, batch_id) == "running"  # never paused or completed early

    _make_due(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "pending" and item.attempts == 2
    assert _approx(item.next_attempt_at, batch_service.utcnow() + timedelta(minutes=10))

    _make_due(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "failed" and item.attempts == 3 and item.next_attempt_at is None
    _drain(batch_env, batch_id)
    assert _batch_status(batch_env, batch_id) == "completed"
    assert pipeline.rendered == ["FAIL always"] * 3


def test_hands_off_retry_succeeds(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["flaky"])
    _set_hands_off(batch_env, batch_id)
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("network blip")

    pipeline.hooks["flaky"] = flaky
    batch_service.run_next_item(batch_id)
    assert _items(batch_env, batch_id)[0].status == "pending"
    _make_due(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "done" and item.attempts == 2 and item.error_message is None


def test_runner_skips_items_waiting_for_a_retry(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["waiting", "ready"])
    _set_hands_off(batch_env, batch_id)
    db = batch_env.Session()
    first = db.query(BatchItem).filter_by(batch_id=batch_id, position=0).one()
    first.next_attempt_at = batch_service.utcnow() + timedelta(minutes=5)
    first.attempts = 1
    db.commit()
    db.close()

    batch_service.run_next_item(batch_id)
    assert pipeline.rendered == ["ready"]
    _drain(batch_env, batch_id)
    assert _batch_status(batch_env, batch_id) == "running"
    assert len(_scheduled_runner(batch_env)) == 1


def test_wake_up_is_not_duplicated(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["waiting"])
    _set_hands_off(batch_env, batch_id)
    db = batch_env.Session()
    item = db.query(BatchItem).filter_by(batch_id=batch_id).one()
    item.next_attempt_at = batch_service.utcnow() + timedelta(minutes=5)
    db.commit()
    db.close()
    for _ in range(3):
        batch_service.run_next_item(batch_id)
    assert len(_scheduled_runner(batch_env)) == 1
    # Starting the batch again wants an immediate run: the wake-up is replaced
    batch_service.enqueue_runner(batch_id, batch_env.redis)
    assert len(_scheduled_runner(batch_env)) == 0
    assert Queue("video_generation", connection=batch_env.redis).count == 1


def test_without_hands_off_failures_stay_failed(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["FAIL once", "two"])
    batch_service.run_next_item(batch_id)
    _drain(batch_env, batch_id)
    first, second = _items(batch_env, batch_id)
    assert first.status == "failed" and first.next_attempt_at is None and first.attempts == 1
    assert second.status == "done"
    assert _batch_status(batch_env, batch_id) == "completed"
    assert _scheduled_runner(batch_env) == []


def test_permanent_errors_are_not_retried(batch_env, pipeline, monkeypatch):
    def no_voice(cfg, user_id):
        raise batch_service.PermanentItemError("Voice not available: deleted")

    monkeypatch.setattr(batch_service, "_resolve_tts", no_voice)
    batch_id = _make_batch(batch_env, ["one"])
    _set_hands_off(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "failed" and item.error_message == "Voice not available: deleted"


def test_retry_by_user_clears_next_attempt(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["x"])
    db = batch_env.Session()
    batch = db.get(Batch, batch_id)
    item = batch.items[0]
    item.status = "failed"
    item.next_attempt_at = batch_service.utcnow()
    db.commit()
    batch_service.retry_item(db, batch, item)
    assert item.status == "pending" and item.next_attempt_at is None
    db.close()


# ── publishing ────────────────────────────────────────────────


def _publish_cfg(env):
    db = env.Session()
    acc = SocialAccount(user_id=env.user_id, provider="youtube", provider_user_id="UC1",
                        access_token_enc="x", status="active")
    db.add(acc)
    db.commit()
    acc_id = acc.id
    db.close()
    return {"enabled": True, "account_id": acc_id, "privacy_status": "private",
            "schedule": {"mode": "immediate"}}


def _set_job(env, batch_id, **fields):
    db = env.Session()
    item = db.query(BatchItem).filter_by(batch_id=batch_id).one()
    job = db.get(PublishJob, item.publish_job_id)
    for k, v in fields.items():
        setattr(job, k, v)
    db.commit()
    db.close()


def test_hands_off_waits_for_uploads_and_retries_failed_ones(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["up"], publish=_publish_cfg(batch_env))
    _set_hands_off(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    assert _items(batch_env, batch_id)[0].status == "publishing"

    _drain(batch_env, batch_id)
    assert _batch_status(batch_env, batch_id) == "running"  # upload still in flight
    (_job, when), = _scheduled_runner(batch_env)
    assert _approx(when, datetime.now(timezone.utc) + batch_service.HANDS_OFF_POLL)

    _set_job(batch_env, batch_id, status="failed", error_code="max_retries", error_message="500s")
    batch_service.run_next_item(batch_id)  # reconciles, then the retry waits 2 minutes
    item = _items(batch_env, batch_id)[0]
    assert item.status == "pending" and item.next_attempt_at is not None
    assert "YouTube upload failed: 500s" in item.error_message

    _make_due(batch_env, batch_id)
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "publishing" and item.attempts == 2
    assert len(pipeline.rendered) == 1 and len(pipeline.publish) == 2  # same video re-uploaded

    _set_job(batch_env, batch_id, status="reconnect_required", error_code="auth_error")
    batch_service.run_next_item(batch_id)
    item = _items(batch_env, batch_id)[0]
    assert item.status == "failed" and "reconnected" in item.error_message
    _drain(batch_env, batch_id)
    assert _batch_status(batch_env, batch_id) == "completed"


def test_non_hands_off_completes_while_upload_in_flight(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["up"], publish=_publish_cfg(batch_env))
    batch_service.run_next_item(batch_id)
    _drain(batch_env, batch_id)
    assert _batch_status(batch_env, batch_id) == "completed"  # unchanged behaviour
    _set_job(batch_env, batch_id, status="failed", error_message="boom")
    db = batch_env.Session()
    batch_service.reconcile_publish_status(db, [db.get(Batch, batch_id)])
    db.close()
    assert _items(batch_env, batch_id)[0].status == "failed"


# ── YouTube quota ─────────────────────────────────────────────


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize("now,expected", [
    (_utc(2026, 1, 15, 12, 0), _utc(2026, 1, 16, 8, 5)),     # PST (UTC-8)
    (_utc(2026, 7, 15, 12, 0), _utc(2026, 7, 16, 7, 5)),     # PDT (UTC-7)
    (_utc(2026, 1, 15, 8, 1), _utc(2026, 1, 15, 8, 5)),      # 00:01 PST: today's reset is still ahead
    (_utc(2026, 1, 15, 8, 5), _utc(2026, 1, 16, 8, 5)),      # exactly at the reset: next day
    (_utc(2026, 3, 8, 6, 0), _utc(2026, 3, 8, 8, 5)),        # DST starts Mar 8: midnight still PST
    (_utc(2026, 3, 8, 12, 0), _utc(2026, 3, 9, 7, 5)),       # ... next midnight is PDT
    (_utc(2026, 11, 1, 12, 0), _utc(2026, 11, 2, 8, 5)),     # DST ends Nov 1
    (datetime(2026, 7, 15, 12, 0), _utc(2026, 7, 16, 7, 5)),  # naive = UTC
])
def test_next_quota_reset(now, expected):
    assert pwt.next_quota_reset(now) == expected


class _Resp:
    def __init__(self, status, reason=None):
        self.status_code = status
        self.text = "err"
        self._reason = reason

    def json(self):
        return {"error": {"code": self.status_code, "errors": [{"reason": self._reason}]}}


@pytest.mark.parametrize("status,reason,exc", [
    (403, "quotaExceeded", youtube.QuotaExceededError),
    (400, "uploadLimitExceeded", youtube.QuotaExceededError),
    (403, "rateLimitExceeded", youtube.RetryableYouTubeError),
    (403, "forbidden", youtube.AuthError),
    (401, "authError", youtube.AuthError),
    (503, "backendError", youtube.RetryableYouTubeError),
])
def test_youtube_error_classification(status, reason, exc):
    with pytest.raises(exc):
        youtube._raise_for_api_error(_Resp(status, reason), "on init")


def test_quota_error_detection():
    assert pwt.is_quota_error(youtube.QuotaExceededError("x"))
    assert pwt.is_quota_error(youtube.YouTubeError("Upload init failed: 403 — Daily quota exceeded"))
    assert not pwt.is_quota_error(youtube.YouTubeError("Upload init failed: 400"))
    assert not pwt.is_quota_error(RuntimeError("quota"))


@pytest.fixture
def publish_env(batch_env, pipeline, monkeypatch, tmp_path):
    """A batch item whose upload is queued; the worker's I/O is faked."""
    monkeypatch.setattr(pwt, "SessionLocal", batch_env.Session)
    monkeypatch.setattr(pwt.Redis, "from_url", staticmethod(lambda url: batch_env.redis))
    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"video")
    monkeypatch.setattr(pwt, "_resolve_video_path", lambda video: (video_file, False))
    monkeypatch.setattr(pwt, "_refresh_if_needed", lambda account, db, r: "token")

    def make(hands_off=True):
        batch_id = _make_batch(batch_env, ["up"], publish=_publish_cfg(batch_env))
        _set_hands_off(batch_env, batch_id, hands_off)
        batch_service.run_next_item(batch_id)
        item = _items(batch_env, batch_id)[0]
        return batch_id, item.publish_job_id

    return SimpleNamespace(make=make, env=batch_env)


def _job(env, job_id):
    db = env.Session()
    job = db.get(PublishJob, job_id)
    db.expunge(job)
    db.close()
    return job


def test_quota_error_waits_for_reset_when_hands_off(publish_env, monkeypatch):
    batch_id, job_id = publish_env.make(hands_off=True)

    def over_quota(*a, **kw):
        raise youtube.QuotaExceededError("YouTube quota exceeded (quotaExceeded) on init")

    monkeypatch.setattr(youtube, "upload_video", over_quota)
    pwt.execute_publish_job(job_id)

    job = _job(publish_env.env, job_id)
    reset = pwt.next_quota_reset()
    assert job.status == "queued" and job.error_code == "quota_exceeded"
    assert job.next_retry_at == reset.replace(tzinfo=None)

    q = Queue("video_publish", connection=publish_env.env.redis)
    reg = ScheduledJobRegistry(queue=q)
    (scheduled_id,) = reg.get_job_ids()
    scheduled = q.fetch_job(scheduled_id)
    assert scheduled.func_name == pwt.PUBLISH_TASK and scheduled.args == (job_id,)
    when = reg.get_scheduled_time(scheduled)
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    assert _approx(when, reset, timedelta(seconds=5))

    # The item stays in progress and shows when it resumes
    db = publish_env.env.Session()
    batch_service.reconcile_publish_status(db, [db.get(Batch, batch_id)])
    db.close()
    assert _items(publish_env.env, batch_id)[0].status == "publishing"
    step = batch_service.quota_wait_step(job)
    local = reset.astimezone()
    assert step == f"Waiting for YouTube quota reset (resumes {local:%H:%M} your time)"
    assert batch_service.publish_wait_until(job) == job.next_retry_at


def test_quota_error_fails_clearly_without_hands_off(publish_env, monkeypatch):
    batch_id, job_id = publish_env.make(hands_off=False)
    monkeypatch.setattr(youtube, "upload_video",
                        lambda *a, **kw: (_ for _ in ()).throw(youtube.YouTubeError("quota exceeded")))
    pwt.execute_publish_job(job_id)
    job = _job(publish_env.env, job_id)
    assert job.status == "failed" and job.error_code == "quota_exceeded"
    assert "midnight Pacific" in job.error_message
    db = publish_env.env.Session()
    account = db.get(SocialAccount, job.social_account_id)
    assert account.status == "active"  # a quota error is not an auth problem
    db.close()


def test_successful_retry_clears_the_wait(publish_env, monkeypatch):
    batch_id, job_id = publish_env.make(hands_off=True)
    _set_job(publish_env.env, batch_id, error_code="quota_exceeded",
             next_retry_at=datetime(2026, 1, 1),
             request_payload={"title": "t", "publish_at": "2020-01-01T00:00:00Z"})
    seen = {}

    def ok(token, path, metadata, on_progress=None):
        seen["publish_at"] = metadata.get("publish_at")
        return {"platform_media_id": "abc", "platform_post_url": "https://youtu.be/abc"}

    monkeypatch.setattr(youtube, "upload_video", ok)
    pwt.execute_publish_job(job_id)
    job = _job(publish_env.env, job_id)
    assert job.status == "published" and job.error_code is None and job.next_retry_at is None
    # a schedule slot missed while waiting moves into the future
    assert seen["publish_at"] > datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_item_response_shows_retry_and_quota_wait(batch_env, pipeline):
    from app.api.routes.batches import _item_response

    batch_id = _make_batch(batch_env, ["a"])
    db = batch_env.Session()
    item = db.query(BatchItem).filter_by(batch_id=batch_id).one()
    item.attempts = 1
    item.next_attempt_at = datetime(2026, 11, 14, 10, 0)
    db.commit()
    resp = _item_response(db, item)
    assert resp.next_attempt_at == "2026-11-14T10:00:00Z"
    assert resp.current_step == "Retrying automatically (attempt 2 of 3)"
    db.close()
