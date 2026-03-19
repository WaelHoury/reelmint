"""Social publishing routes — OAuth, account management, and publish jobs."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.schemas.publish import (
    ConnectedAccountListResponse,
    ConnectedAccountResponse,
    CreatePublishJobRequest,
    PublishJobListResponse,
    PublishJobResponse,
    StartOAuthRequest,
    StartOAuthResponse,
)
from app.api.services.publish_service import publish_service, PublishServiceError
from app.core.config import settings
from app.db.base import get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/publish", tags=["publish"])
video_publish_router = APIRouter(prefix="/videos", tags=["publish"])


def _account_to_response(account) -> ConnectedAccountResponse:
    return ConnectedAccountResponse(
        id=account.id,
        provider=account.provider,
        display_name=account.display_name,
        username=account.username,
        avatar_url=account.avatar_url,
        status=account.status,
        created_at=account.created_at.isoformat() if account.created_at else "",
    )


def _job_to_response(job) -> PublishJobResponse:
    return PublishJobResponse(
        id=job.id,
        video_id=job.video_id,
        account_id=job.social_account_id,
        provider=job.provider,
        mode=job.mode,
        status=job.status,
        platform_post_url=job.platform_post_url,
        error_message=job.error_message,
        retry_count=job.retry_count,
        created_at=job.created_at.isoformat() if job.created_at else "",
        published_at=job.published_at.isoformat() if job.published_at else None,
    )


# ── Account management ────────────────────────────────────────


@router.get("/accounts", response_model=ConnectedAccountListResponse)
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accounts = publish_service.list_accounts(user.id, db)
    return ConnectedAccountListResponse(
        accounts=[_account_to_response(a) for a in accounts]
    )


@router.post("/oauth/{provider}/start", response_model=StartOAuthResponse)
def start_oauth(
    provider: str,
    body: StartOAuthRequest,
    user: User = Depends(get_current_user),
):
    try:
        auth_url = publish_service.start_oauth(user.id, provider, body.return_to)
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return StartOAuthResponse(auth_url=auth_url)


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    """Handle OAuth callback redirect from the provider.

    This endpoint is NOT JWT-authenticated — the browser hits it directly
    from the OAuth provider redirect. Security relies on the cryptographic
    state token stored in Redis.
    """
    # Determine frontend origin for redirect
    origins = settings.cors_origins.split(",")
    frontend_origin = origins[0].strip() if origins else "http://localhost:3000"

    if error:
        params = urlencode({"oauth": "error", "provider": provider, "message": error})
        return RedirectResponse(url=f"{frontend_origin}/account?{params}")

    if not code or not state:
        params = urlencode({"oauth": "error", "provider": provider, "message": "Missing code or state"})
        return RedirectResponse(url=f"{frontend_origin}/account?{params}")

    try:
        # Peek at return_to before consuming state
        return_to = publish_service.get_return_to(state)
        account = publish_service.handle_oauth_callback(provider, code, state, db)
        logger.info("OAuth callback success: user connected %s account '%s'", provider, account.display_name)
        params = urlencode({"oauth": "success", "provider": provider})
        return RedirectResponse(url=f"{frontend_origin}{return_to}?{params}")
    except PublishServiceError as e:
        logger.warning("OAuth callback error: %s", e)
        params = urlencode({"oauth": "error", "provider": provider, "message": str(e)})
        return RedirectResponse(url=f"{frontend_origin}/account?{params}")


@router.delete("/accounts/{account_id}", status_code=204)
def disconnect_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        publish_service.disconnect_account(account_id, user.id, db)
    except PublishServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Job management ────────────────────────────────────────────


@router.get("/jobs/{job_id}", response_model=PublishJobResponse)
def get_publish_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job = publish_service.get_publish_job(job_id, user.id, db)
    except PublishServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _job_to_response(job)


@router.post("/jobs/{job_id}/retry", response_model=PublishJobResponse)
def retry_publish_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job = publish_service.retry_publish_job(job_id, user.id, db)
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _job_to_response(job)


# ── Video publish routes (mounted under /api/videos) ─────────


@video_publish_router.post("/{video_id}/publish", response_model=PublishJobResponse, status_code=201)
def publish_video(
    video_id: int,
    body: CreatePublishJobRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job = publish_service.create_publish_job(
            user_id=user.id,
            video_id=video_id,
            account_id=body.account_id,
            mode=body.mode,
            metadata=body.metadata,
            db=db,
        )
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _job_to_response(job)


@video_publish_router.get("/{video_id}/publish-jobs", response_model=PublishJobListResponse)
def list_video_publish_jobs(
    video_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jobs = publish_service.list_publish_jobs(video_id, user.id, db)
    return PublishJobListResponse(jobs=[_job_to_response(j) for j in jobs])
