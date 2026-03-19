"""Fernet-based encryption for social account OAuth tokens."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance (lazy singleton)."""
    global _fernet
    if _fernet is not None:
        return _fernet

    from app.core.config import settings

    key = settings.social_token_encryption_key
    if key:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    else:
        if settings.environment == "production":
            raise ValueError(
                "SOCIAL_TOKEN_ENCRYPTION_KEY is required in production. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        # Dev fallback: derive a Fernet key from jwt_secret_key
        logger.warning(
            "SOCIAL_TOKEN_ENCRYPTION_KEY not set — deriving dev-only key from JWT_SECRET_KEY. "
            "Do NOT use this in production."
        )
        digest = hashlib.sha256(settings.jwt_secret_key.encode()).digest()
        derived_key = base64.urlsafe_b64encode(digest)
        _fernet = Fernet(derived_key)

    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns base64-encoded Fernet ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted token. Raises ValueError on bad key/data."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError(f"Failed to decrypt token: {e}") from e
