"""The anonymous browser session a hold belongs to.

A hold is created with no contact details, so before this there was nothing on the server that said two
hold requests came from the same browser: the funnel had to volunteer `replace_hold_id`, and a tab that
had lost its pointer, or a second tab that never had one, would stack a second hold instead of swapping.
The cookie makes "one browser, one date" something the server knows rather than something the client is
trusted to ask for.

It identifies a browser, not a person. It carries no claims, grants no access, and a visitor who clears it
simply falls back to the IP-based caps.
"""

import secrets
import string

COOKIE_NAME = "encore_session"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60
TOKEN_BYTES = 18
# A visitor sends this back to us, so it is bounded and checked before it reaches a query.
ALLOWED = set(string.ascii_letters + string.digits + "-_")
MIN_LENGTH = 16
MAX_LENGTH = 64


def new_session_id() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def clean_session_id(raw: str | None) -> str | None:
    """A cookie we did not mint, or one that has been edited, is not a session. None means issue a new one."""
    if raw is None or not MIN_LENGTH <= len(raw) <= MAX_LENGTH:
        return None
    return raw if all(char in ALLOWED for char in raw) else None
