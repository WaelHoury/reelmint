"""Business logic for social publishing — OAuth, account management, and job creation."""

import json
import logging
import secrets
from datetime import datetime, timedelta

from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import SocialAccount, PublishJob, Video
from app.api.services.token_encryption import encrypt_token, decrypt_token
from app.social import youtube

logger = logging.getLogger(__name__)

OAUTH_STATE_TTL = 600  # 10 minutes
SUPPORTED_PROVIDERS = {"youtube"}


class PublishServiceError(Exception):
    """Base error for publish service."""


class PublishService:
    def __init__(self):
        self.redis = Redis.from_url(settings.redis_url)
        self.publish_queue = Queue("video_publish", connection=self.redis)

    def _get_provider_module(self, provider: str):
        """Get the provider module by name."""
        if provider == "youtube":
            return youtube
        raise PublishServiceError(f"Unsupported provider: {provider}")

    def _validate_provider(self, provider: str):
        """Check that the provider is supported and enabled."""
        if provider not in SUPPORTED_PROVIDERS:
            raise PublishServiceError(f"Unsupported provider: {provider}")
        enabled = [p.strip() for p in settings.social_enabled_providers.split(",") if p.strip()]
        if provider not in enabled:
            raise PublishServiceError(f"Provider '{provider}' is not enabled")

    # ── OAuth ─────────────────────────────────────────────────

    def start_oauth(self, user_id: int, provider: str, return_to: str = "/account") -> str:
        """Generate OAuth state, store in Redis, return auth URL."""
        self._validate_provider(provider)
        mod = self._get_provider_module(provider)

        state = secrets.token_urlsafe(32)
        state_data = json.dumps({"user_id": user_id, "provider": provider, "return_to": return_to})
        self.redis.setex(f"oauth_state:{state}", OAUTH_STATE_TTL, state_data)

        return mod.build_auth_url(state)

    def handle_oauth_callback(self, provider: str, code: str, state: str, db: Session) -> SocialAccount:
        """Validate state, exchange code, save account."""
        # One-time state consumption
        raw = self.redis.getdel(f"oauth_state:{state}")
        if not raw:
            raise PublishServiceError("Invalid or expired OAuth state")

        state_data = json.loads(raw)
        if state_data.get("provider") != provider:
            raise PublishServiceError("OAuth state provider mismatch")

        user_id = state_data["user_id"]
        mod = self._get_provider_module(provider)

        # Exchange code for tokens
        tokens = mod.exchange_code(code)
        access_token = tokens["access_token"]
        refresh_tok = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)

        # Fetch identity
        identity = mod.fetch_identity(access_token)

        # Encrypt tokens
        access_enc = encrypt_token(access_token)
        refresh_enc = encrypt_token(refresh_tok) if refresh_tok else None

        # Upsert social account
        account = db.query(SocialAccount).filter_by(
            user_id=user_id,
            provider=provider,
            provider_user_id=identity["provider_user_id"],
        ).first()

        now = datetime.utcnow()
        if account:
            account.access_token_enc = access_enc
            account.refresh_token_enc = refresh_enc
            account.scopes = tokens.get("scope", "")
            account.token_expires_at = now + timedelta(seconds=expires_in)
            account.status = "active"
            account.display_name = identity.get("display_name", account.display_name)
            account.username = identity.get("username", account.username)
            account.avatar_url = identity.get("avatar_url", account.avatar_url)
            account.provider_metadata = identity.get("metadata")
            account.updated_at = now
        else:
            account = SocialAccount(
                user_id=user_id,
                provider=provider,
                provider_user_id=identity["provider_user_id"],
                display_name=identity.get("display_name", ""),
                username=identity.get("username", ""),
                avatar_url=identity.get("avatar_url", ""),
                access_token_enc=access_enc,
                refresh_token_enc=refresh_enc,
                scopes=tokens.get("scope", ""),
                token_expires_at=now + timedelta(seconds=expires_in),
                status="active",
                provider_metadata=identity.get("metadata"),
            )
            db.add(account)

        db.commit()
        db.refresh(account)
        return account

    def get_return_to(self, state: str) -> str:
        """Peek at the return_to from a stored OAuth state without consuming it."""
        raw = self.redis.get(f"oauth_state:{state}")
        if not raw:
            return "/account"
        data = json.loads(raw)
        return data.get("return_to", "/account")

    # ── Account management ────────────────────────────────────

    def list_accounts(self, user_id: int, db: Session) -> list[SocialAccount]:
        """List all connected social accounts for a user."""
        return db.query(SocialAccount).filter_by(user_id=user_id).order_by(SocialAccount.created_at.desc()).all()

    def disconnect_account(self, account_id: int, user_id: int, db: Session) -> None:
        """Disconnect a social account — revoke token and delete."""
        account = db.query(SocialAccount).filter_by(id=account_id, user_id=user_id).first()
        if not account:
            raise PublishServiceError("Account not found")

        # Best-effort token revocation
        try:
            mod = self._get_provider_module(account.provider)
            if account.refresh_token_enc:
                mod.revoke_token(decrypt_token(account.refresh_token_enc))
            elif account.access_token_enc:
                mod.revoke_token(decrypt_token(account.access_token_enc))
        except Exception as e:
            logger.warning("Token revocation failed for account %s: %s", account_id, e)

        db.delete(account)
        db.commit()

    # ── Publishing ────────────────────────────────────────────

    def create_publish_job(
        self,
        user_id: int,
        video_id: int,
        account_id: int,
        mode: str,
        metadata: dict,
        db: Session,
    ) -> PublishJob:
        """Create a publish job and enqueue it."""
        # Verify user owns the video
        video = db.query(Video).filter_by(id=video_id, user_id=user_id).first()
        if not video:
            raise PublishServiceError("Video not found")

        # Verify user owns the account and it's active
        account = db.query(SocialAccount).filter_by(id=account_id, user_id=user_id).first()
        if not account:
            raise PublishServiceError("Social account not found")
        if account.status != "active":
            raise PublishServiceError(f"Social account is {account.status} — please reconnect")

        job = PublishJob(
            user_id=user_id,
            video_id=video_id,
            social_account_id=account_id,
            provider=account.provider,
            mode=mode,
            request_payload=metadata,
            status="queued",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # Enqueue on the publish worker queue
        self.publish_queue.enqueue(
            "app.api.services.publish_worker_task.execute_publish_job",
            job.id,
            job_timeout="30m",
        )
        logger.info("Publish job %s enqueued for video %s → %s", job.id, video_id, account.provider)
        return job

    def get_publish_job(self, job_id: int, user_id: int, db: Session) -> PublishJob:
        """Get a publish job by ID, verifying ownership."""
        job = db.query(PublishJob).filter_by(id=job_id, user_id=user_id).first()
        if not job:
            raise PublishServiceError("Publish job not found")
        return job

    def list_publish_jobs(self, video_id: int, user_id: int, db: Session) -> list[PublishJob]:
        """List all publish jobs for a video."""
        return (
            db.query(PublishJob)
            .filter_by(video_id=video_id, user_id=user_id)
            .order_by(PublishJob.created_at.desc())
            .all()
        )

    def retry_publish_job(self, job_id: int, user_id: int, db: Session) -> PublishJob:
        """Retry a failed publish job."""
        job = db.query(PublishJob).filter_by(id=job_id, user_id=user_id).first()
        if not job:
            raise PublishServiceError("Publish job not found")
        if job.status not in ("failed", "reconnect_required"):
            raise PublishServiceError(f"Cannot retry job with status '{job.status}'")

        # If reconnect_required, check if account has been reconnected
        if job.status == "reconnect_required":
            account = db.query(SocialAccount).filter_by(id=job.social_account_id).first()
            if not account or account.status != "active":
                raise PublishServiceError("Social account must be reconnected before retrying")

        job.status = "queued"
        job.error_code = None
        job.error_message = None
        job.retry_count += 1
        job.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(job)

        self.publish_queue.enqueue(
            "app.api.services.publish_worker_task.execute_publish_job",
            job.id,
            job_timeout="30m",
        )
        logger.info("Publish job %s re-enqueued (retry #%s)", job.id, job.retry_count)
        return job


# Module-level singleton
publish_service = PublishService()
