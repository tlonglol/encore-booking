"""Every frontend script must parse.

A syntax error in one of these files is silent: the browser drops the whole file, the page renders a
spinner forever, and nothing in the Python suite notices because none of it ever loads the page. That is
exactly how `confirm.js` shipped broken - two `replaceChildren(` calls closed with `]);` - and stayed
broken until someone actually completed a booking in a browser and watched the confirmation never
arrive. The money path ends on that page, so it is worth one cheap check.
"""

import pathlib
import shutil
import subprocess

import pytest

FRONTEND_JS = sorted((pathlib.Path(__file__).resolve().parent.parent / "frontend" / "js").glob("*.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", FRONTEND_JS, ids=lambda p: p.name)
def test_the_script_parses(script: pathlib.Path) -> None:
    result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"{script.name} does not parse:\n{result.stderr}"


def test_the_scripts_are_actually_being_checked() -> None:
    """A glob that matched nothing would make every test above vacuously pass."""
    assert len(FRONTEND_JS) >= 10
    assert any(p.name == "confirm.js" for p in FRONTEND_JS)
