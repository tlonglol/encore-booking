import hashlib
import hmac
from datetime import datetime

COOKIE_NAME = "encore_admin"
SESSION_SECONDS = 12 * 60 * 60


def password_matches(candidate: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _signature(expires: int, secret: str) -> str:
    return hmac.new(secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def issue_token(secret: str, now: datetime) -> str:
    expires = int(now.timestamp()) + SESSION_SECONDS
    return f"{expires}.{_signature(expires, secret)}"


def token_valid(token: str | None, secret: str, now: datetime) -> bool:
    if not token or not secret:
        return False
    expires_text, _, signature = token.partition(".")
    if not expires_text.isdigit():
        return False
    expires = int(expires_text)
    if not hmac.compare_digest(signature, _signature(expires, secret)):
        return False
    return expires > now.timestamp()
