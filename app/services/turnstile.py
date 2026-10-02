"""Cloudflare Turnstile on the Confirm action.

Scaffolded and off. With `TURNSTILE_ENABLED` false, which is the default, `verify` returns at once, the
funnel never loads Cloudflare's script and nothing here touches the network, so the money path behaves
exactly as it did before this file existed. Turning it on is a decision for the day holds start being
abused by something a cookie and an IP cannot see.
"""

import logging

import httpx

from app.config import Settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
REQUEST_TIMEOUT_SECONDS = 5.0

log = logging.getLogger(__name__)


def verify(settings: Settings, token: str | None, client_ip: str | None) -> bool:
    """True when the hold may proceed. Off, that is always; on, it is Cloudflare's answer.

    A Cloudflare outage fails closed rather than open: with the flag on, the owner has decided holds need
    a challenge, and quietly dropping it would make the flag a suggestion.
    """
    if not settings.turnstile_enabled:
        return True
    if not token or not settings.turnstile_secret_key:
        return False
    data = {"secret": settings.turnstile_secret_key, "response": token}
    if client_ip:
        data["remoteip"] = client_ip
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(VERIFY_URL, data=data)
    except httpx.HTTPError as error:
        log.warning("Turnstile verification unreachable: %s", type(error).__name__)
        return False
    if response.status_code != 200:
        log.warning("Turnstile verification returned %s", response.status_code)
        return False
    return bool(response.json().get("success"))
