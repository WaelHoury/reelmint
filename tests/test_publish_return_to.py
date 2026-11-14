"""Open-redirect protection for the YouTube OAuth callback's return_to path."""

from urllib.parse import urlsplit

import pytest

from app.api.schemas.publish import (
    DEFAULT_RETURN_TO,
    StartOAuthRequest,
    build_frontend_redirect,
    safe_return_to,
)

ORIGIN = "http://localhost:3000"


@pytest.mark.parametrize(
    "value",
    ["/account", "/", "/videos/42/edit", "/library_view", "/a-b/c_d/"],
)
def test_safe_paths_are_kept(value):
    assert safe_return_to(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "@evil.com/x",
        "@evil.com",
        ".evil.com",
        "evil.com",
        "account",
        "//evil.com",
        "//evilcom",
        "/\\evil.com",
        "\\\\evil.com",
        "https://evil.com",
        "javascript:alert(1)",
        "/account?next=https://evil.com",
        "/account#frag",
        "/acc ount",
        "/account\n",
        "/../etc",
        "/%2F%2Fevil.com",
        "",
        None,
        123,
    ],
)
def test_unsafe_values_fall_back_to_default(value):
    assert safe_return_to(value) == DEFAULT_RETURN_TO


def test_schema_sanitizes_return_to():
    assert StartOAuthRequest(return_to="@evil.com/x").return_to == DEFAULT_RETURN_TO
    assert StartOAuthRequest(return_to="/videos/3").return_to == "/videos/3"
    assert StartOAuthRequest().return_to == DEFAULT_RETURN_TO


@pytest.mark.parametrize("return_to", ["@evil.com/x", "//evil.com", "/account", None])
def test_redirect_never_leaves_frontend_origin(return_to):
    url = build_frontend_redirect(ORIGIN, return_to, {"oauth": "success", "provider": "youtube"})
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}" == ORIGIN
    assert parts.username is None


def test_redirect_builds_single_query_string():
    url = build_frontend_redirect(ORIGIN, "/account", {"oauth": "error", "message": "a b&c"})
    assert url == f"{ORIGIN}/account?oauth=error&message=a+b%26c"
    assert url.count("?") == 1


def test_redirect_without_params_has_no_question_mark():
    assert build_frontend_redirect(ORIGIN, "/account", {}) == f"{ORIGIN}/account"
