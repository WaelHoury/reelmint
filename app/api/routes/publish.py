"""Social publishing routes — OAuth, account management, and publish jobs."""

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
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
    build_frontend_redirect,
)
from app.api.services.publish_service import OAUTH_STATE_TTL, publish_service, PublishServiceError
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


# Browser-binding cookie for the OAuth flow. Set on the /start response (the
# frontend calls it through the same host, e.g. the Vite /api proxy) and sent
# back by the browser on the provider's top-level GET redirect to the callback
# (SameSite=Lax allows that). Cookies are not port-scoped, so a frontend on
# localhost:3000 and a callback on localhost:8000 share it.
OAUTH_NONCE_COOKIE = "publish_oauth_nonce"
OAUTH_COOKIE_PATH = "/api/publish/oauth"


def _frontend_origin() -> str:
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    return origins[0] if origins else "http://localhost:3000"


def _redirect(return_to: str, params: dict) -> RedirectResponse:
    response = RedirectResponse(url=build_frontend_redirect(_frontend_origin(), return_to, params))
    # The nonce is single-use: clear it whatever the outcome.
    response.delete_cookie(OAUTH_NONCE_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


@router.post("/oauth/{provider}/start", response_model=StartOAuthResponse)
def start_oauth(
    provider: str,
    body: StartOAuthRequest,
    response: Response,
    user: User = Depends(get_current_user),
):
    nonce = secrets.token_urlsafe(32)
    try:
        auth_url = publish_service.start_oauth(user.id, provider, body.return_to, browser_nonce=nonce)
    except PublishServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    response.set_cookie(
        OAUTH_NONCE_COOKIE,
        nonce,
        max_age=OAUTH_STATE_TTL,
        path=OAUTH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.youtube_redirect_uri.startswith("https://"),
    )
    return StartOAuthResponse(auth_url=auth_url)


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: str,
    code: str = "",
    state: str = "",
    error: str = "",
    oauth_nonce: Optional[str] = Cookie(default=None, alias=OAUTH_NONCE_COOKIE),
    db: Session = Depends(get_db),
):
    """Handle OAuth callback redirect from the provider.

    This endpoint is not user-authenticated — the browser hits it directly
    from the OAuth provider redirect. Security relies on the single-use,
    short-lived state token stored in Redis, which must match the HttpOnly
    nonce cookie set on this browser when the flow was started.
    """
    if error:
        return _redirect("/account", {"oauth": "error", "provider": provider, "message": error})

    if not code or not state:
        return _redirect("/account", {"oauth": "error", "provider": provider, "message": "Missing code or state"})

    try:
        account, return_to = publish_service.handle_oauth_callback(
            provider, code, state, db, browser_nonce=oauth_nonce
        )
        logger.info("OAuth callback success: user connected %s account '%s'", provider, account.display_name)
        return _redirect(return_to, {"oauth": "success", "provider": provider})
    except PublishServiceError as e:
        logger.warning("OAuth callback error: %s", e)
        return _redirect("/account", {"oauth": "error", "provider": provider, "message": str(e)})


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
