"""Cache headers on the frontend files.

Builds its own mount because the real one only registers when SERVE_FRONTEND is on, and the tests
turn that off.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import FRONTEND_DIR, FrontendFiles


@pytest.fixture(scope="module")
def frontend() -> TestClient:
    if not FRONTEND_DIR.is_dir():
        pytest.skip("no frontend directory in this checkout")
    served = FastAPI()
    served.mount("/", FrontendFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return TestClient(served, base_url="https://testserver")


@pytest.mark.parametrize("path", ["/js/book.js", "/js/card.js", "/css/encore.css"])
def test_scripts_and_styles_are_held_briefly(frontend: TestClient, path: str) -> None:
    response = frontend.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=300"


@pytest.mark.parametrize("path", ["/index.html", "/book.html"])
def test_markup_is_never_held(frontend: TestClient, path: str) -> None:
    # HTML has to revalidate so a deploy shows up on the next load.
    response = frontend.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
