"""Batch runner: ordering, chaining, failure handling, pause, locking, publishing, recovery.

The heavy pipeline (LLM script, clip search, render, YouTube upload) is
monkeypatched; Redis is fakeredis and the DB is in-memory SQLite.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.services import batch_service
from app.api.services.batch_service import vs  # the video_service module
from app.api.services.session_store import session_store
from app.db.models import Batch, BatchItem, PublishJob, SocialAccount, Video


@pytest.fixture
def pipeline(batch_env, monkeypatch, tmp_path):
    """Fake pipeline that records the order items are processed in."""
    calls = SimpleNamespace(rendered=[], hooks={}, publish=[])

    monkeypatch.setattr(batch_service, "_resolve_tts", lambda cfg, user_id: None)

    def fake_generate_script(session):
        if session.input_type == "prompt":
            session.script_text = f"Narration about {session.prompt}. It is fun."
        session.word_count = len(session.script_text.split())
        session.status = "draft"
        session_store.update(session)
        return session

    def fake_clips(session_id):
        s = session_store.get(session_id)
        s.clips = [{"index": 0, "keywords": ["x"], "visual_description": "x",
                    "author": "Ana", "author_url": "https://pexels.com/1", "search_status": "ready"}]
        s.status = "clips_ready"
        session_store.update(s)

    def fake_render(session_id):
        s = session_store.get(session_id)
        assert s.status == "generating"
        text = s.prompt or s.script_text
        calls.rendered.append(text)
        hook = calls.hooks.get(text)
        if hook:
            hook()
        if "FAIL" in text:
            s.status = "failed"
            s.error_message = "Video generation failed. Check server logs for details."
            session_store.update(s)
            raise RuntimeError("ffmpeg exploded")
        out = tmp_path / f"u{s.user_id}_{s.session_id[:8]}.mp4"
        out.write_bytes(b"video")
        db = batch_env.Session()
        db.add(Video(user_id=s.user_id, title=s.title, input_type=s.input_type,
                     prompt=s.prompt, script_text=s.script_text, duration_seconds=s.duration_seconds,
                     orientation=s.orientation, output_filename=out.name))
        db.commit()
        db.close()
        s.status = "completed"
        s.output_path = str(out)
        session_store.update(s)

    monkeypatch.setattr(vs.video_service, "generate_script", fake_generate_script)
    monkeypatch.setattr(vs, "generate_clips_task", fake_clips)
    monkeypatch.setattr(vs, "generate_video_task", fake_render)
    monkeypatch.setattr(batch_service, "generate_metadata",
                        lambda narration, topic, style=None: {"title": f"T: {topic}", "description": "D", "tags": ["a"]})

    class FakePublishService:
        def create_publish_job(self, user_id, video_id, account_id, mode, metadata, db):
            calls.publish.append(metadata)
            job = PublishJob(user_id=user_id, video_id=video_id, social_account_id=account_id,
                             provider="youtube", mode=mode, request_payload=metadata, status="queued")
            db.add(job)
            db.commit()
            return job

    monkeypatch.setattr(batch_service, "_get_publish_service", lambda: FakePublishService())
    return calls


def _make_batch(env, texts, status="running", publish=None, settings=None):
    db = env.Session()
    batch = Batch(user_id=env.user_id, name="b", status=status,
                  settings=settings or {"orientation": "portrait", "duration_seconds": 30},
                  publish=publish or {"enabled": False})
    db.add(batch)
    for i, text in enumerate(texts):
        batch.items.append(BatchItem(position=i, input_type="prompt", input_text=text, status="pending"))
    db.commit()
    batch_id = batch.id
    db.close()
    return batch_id


def _queued_runner_jobs(env):
    from rq import Queue
    return Queue("video_generation", connection=env.redis).jobs


def _drain(env, batch_id, max_runs=20):
    """Act as the worker: run queued runner jobs until the chain stops."""
    from rq import Queue
    q = Queue("video_generation", connection=env.redis)
    runs = 0
    while q.count and runs < max_runs:
        job = q.jobs[0]
        q.remove(job)
        assert job.func_name == batch_service.RUNNER_JOB
        batch_service.run_next_item(*job.args)
        runs += 1
    return runs


def _items(env, batch_id):
    db = env.Session()
    items = db.query(BatchItem).filter_by(batch_id=batch_id).order_by(BatchItem.position).all()
    db.expunge_all()
    db.close()
    return items


def _batch_status(env, batch_id):
    db = env.Session()
    status = db.get(Batch, batch_id).status
    db.close()
    return status


def test_runs_items_in_order_and_chains(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one", "two", "three"])
    batch_service.run_next_item(batch_id)

    assert pipeline.rendered == ["one"]
    assert len(_queued_runner_jobs(batch_env)) == 1  # chained to the next item
    _drain(batch_env, batch_id)

    assert pipeline.rendered == ["one", "two", "three"]
    items = _items(batch_env, batch_id)
    assert [i.status for i in items] == ["done"] * 3
    assert all(i.video_id and i.session_id and i.attempts == 1 for i in items)
    assert all(i.started_at and i.finished_at for i in items)
    assert _batch_status(batch_env, batch_id) == "completed"
    assert _queued_runner_jobs(batch_env) == []
    assert batch_env.redis.get("batch_runner:%d" % batch_id) is None  # lock released


def test_failure_marks_item_failed_and_batch_continues(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one", "FAIL two", "three"])
    batch_service.run_next_item(batch_id)
    _drain(batch_env, batch_id)

    items = _items(batch_env, batch_id)
    assert [i.status for i in items] == ["done", "failed", "done"]
    assert items[1].error_message.startswith("Rendering failed")
    assert items[1].attempts == 1
    assert _batch_status(batch_env, batch_id) == "completed"


def test_pause_lets_current_item_finish_then_stops(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one", "two"])

    def pause_now():
        db = batch_env.Session()
        db.get(Batch, batch_id).status = "paused"
        db.commit()
        db.close()

    pipeline.hooks["one"] = pause_now
    batch_service.run_next_item(batch_id)

    assert [i.status for i in _items(batch_env, batch_id)] == ["done", "pending"]
    assert _queued_runner_jobs(batch_env) == []  # chain stopped
    assert _batch_status(batch_env, batch_id) == "paused"

    # A runner that starts on a paused batch does nothing
    batch_service.run_next_item(batch_id)
    assert pipeline.rendered == ["one"]


def test_second_runner_is_blocked_by_lock(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one"])
    batch_env.redis.set("batch_runner:%d" % batch_id, "local:other", ex=60)
    batch_service.run_next_item(batch_id)
    assert pipeline.rendered == []
    assert _items(batch_env, batch_id)[0].status == "pending"


def test_enqueue_runner_is_deduplicated(batch_env):
    first = batch_service.enqueue_runner(7)
    second = batch_service.enqueue_runner(7)
    assert first == second
    assert len(_queued_runner_jobs(batch_env)) == 1


def test_publish_schedules_items_and_reconciles(batch_env, pipeline):
    db = batch_env.Session()
    account = SocialAccount(user_id=batch_env.user_id, provider="youtube", provider_user_id="c",
                            access_token_enc="x", status="active")
    db.add(account)
    db.commit()
    account_id = account.id
    db.close()

    start = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0)
    publish = {
        "enabled": True, "account_id": account_id, "privacy_status": "public",
        "extra_tags": ["shorts"], "description_footer": "Follow us!",
        "schedule": {"mode": "interval", "start_at": start.isoformat() + "Z", "interval_minutes": 60},
    }
    batch_id = _make_batch(batch_env, ["one", "two"], publish=publish)
    batch_service.run_next_item(batch_id)
    _drain(batch_env, batch_id)

    items = _items(batch_env, batch_id)
    assert [i.status for i in items] == ["publishing", "publishing"]
    assert items[0].scheduled_publish_at == start
    assert items[1].scheduled_publish_at == start + timedelta(minutes=60)
    meta = pipeline.publish[0]
    assert meta["publish_at"] == start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert meta["tags"][0] == "shorts"
    assert meta["description"].startswith("D\n\nFollow us!")
    assert meta["privacy_status"] == "public" and meta["category_id"] == "22"

    # Publish worker finishes: one success, one failure
    db = batch_env.Session()
    ok = db.get(PublishJob, items[0].publish_job_id)
    ok.status, ok.platform_post_url = "published", "https://www.youtube.com/watch?v=abc"
    bad = db.get(PublishJob, items[1].publish_job_id)
    bad.status, bad.error_message = "failed", "quotaExceeded"
    db.commit()
    batch = db.get(Batch, batch_id)
    assert batch_service.reconcile_publish_status(db, [batch]) is True
    db.close()

    items = _items(batch_env, batch_id)
    assert items[0].status == "published"
    assert items[0].youtube_url == "https://www.youtube.com/watch?v=abc"
    assert items[1].status == "failed" and "quotaExceeded" in items[1].error_message

    # Retrying the failed publish reuses the rendered video (no second render)
    db = batch_env.Session()
    batch = db.get(Batch, batch_id)
    batch_service.retry_item(db, batch, db.get(BatchItem, items[1].id))
    batch_service.start_batch(db, batch)
    db.close()
    _drain(batch_env, batch_id)
    assert pipeline.rendered == ["one", "two"]
    assert len(pipeline.publish) == 3
    assert _items(batch_env, batch_id)[1].status == "publishing"


def test_recovery_fails_stuck_item_and_requeues(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one", "two"])
    db = batch_env.Session()
    item = db.query(BatchItem).filter_by(batch_id=batch_id, position=0).one()
    item.status = "rendering"
    item.started_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    db.close()

    stats = batch_service.recover_stuck_batches()
    assert stats == {"failed_items": 1, "requeued": 1}
    items = _items(batch_env, batch_id)
    assert items[0].status == "failed"
    assert items[0].error_message == "Worker stopped while processing"
    assert len(_queued_runner_jobs(batch_env)) == 1

    _drain(batch_env, batch_id)
    assert [i.status for i in _items(batch_env, batch_id)] == ["failed", "done"]


def test_recovery_leaves_batch_with_live_runner_alone(batch_env, pipeline):
    batch_id = _make_batch(batch_env, ["one"])
    db = batch_env.Session()
    item = db.query(BatchItem).filter_by(batch_id=batch_id).one()
    item.status = "rendering"
    item.started_at = datetime.utcnow() - timedelta(minutes=10)
    db.commit()
    db.close()
    batch_env.redis.set("batch_runner:%d" % batch_id, "local:alive", ex=60)

    assert batch_service.recover_stuck_batches() == {"failed_items": 0, "requeued": 0}
    assert _items(batch_env, batch_id)[0].status == "rendering"
