"""Rate limiter instance shared across route modules."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Keyed by client IP — works behind reverse proxy with X-Forwarded-For header
limiter = Limiter(key_func=get_remote_address)
