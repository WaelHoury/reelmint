"""Fernet-based encryption for social account OAuth tokens.

Key source, in order:
1. ``SOCIAL_TOKEN_ENCRYPTION_KEY`` env var, if set.
2. A key generated once and persisted to ``<CACHE_DIR>/.social_token_key``
   (file mode 0600), reused on later runs and shared by the API and workers
   as long as they see the same cache directory.
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.social.youtube import AuthError

logger = logging.getLogger(__name__)

KEY_FILENAME = ".social_token_key"

_fernet = None


class TokenDecryptionError(AuthError, ValueError):
    """Stored token can't be decrypted (e.g. the encryption key changed).

    Subclasses AuthError so the publish worker marks the job as
    "reconnect_required" instead of failing unexpectedly.
    """


def _key_file_path() -> Path:
    from app.core.config import settings

    return Path(settings.cache_dir) / KEY_FILENAME


def _load_or_create_key_file(path: Path) -> bytes:
    """Read the persisted key, or generate and atomically create it."""
    if path.exists():
        return path.read_bytes().strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it first — use theirs.
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    logger.info("Generated social token encryption key at %s", path)
    return key


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
        _fernet = Fernet(_load_or_create_key_file(_key_file_path()))

    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string. Returns base64-encoded Fernet ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted token.

    Raises TokenDecryptionError (a ValueError and AuthError) on bad key/data.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise TokenDecryptionError(
            "Stored account token could not be decrypted (the encryption key may have changed). "
            "Please disconnect and reconnect the account."
        ) from e
