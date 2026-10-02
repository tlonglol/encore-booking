from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings

router = APIRouter()


class ConfigOut(BaseModel):
    """The public flags the static frontend cannot know on its own. No secrets: the Turnstile site key is
    public by design, and nothing here reveals whether credentials are configured."""

    test_mode: bool
    turnstile_enabled: bool
    turnstile_site_key: str


@router.get("/api/config")
def get_config() -> ConfigOut:
    settings = get_settings()
    return ConfigOut(
        test_mode=settings.test_mode_enabled,
        turnstile_enabled=settings.turnstile_enabled,
        turnstile_site_key=settings.turnstile_site_key if settings.turnstile_enabled else "",
    )
