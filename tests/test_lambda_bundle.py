"""What goes in the deployment zip.

The failure this guards against is quiet and only shows up on AWS: someone adds a dependency, uses it
in `app/`, the local venv has it, every test passes, and the first request to the Lambda is an
ImportError because requirements-lambda.txt was never updated. So the imports in `app/` are read back
against that file, and its pins are held to requirements.txt so the deploy can never run a different
version of a library than the suite tested.
"""

import ast
import pathlib
import sys
from importlib.metadata import packages_distributions

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNTIME_REQUIREMENTS = REPO / "requirements-lambda.txt"
ALL_REQUIREMENTS = REPO / "requirements.txt"

# In the venv and in CI, never in the Lambda: the runtime is the server, migrations are run from the
# laptop against Neon's direct connection string, and tests do not ship.
DEV_ONLY = ("uvicorn", "alembic", "pytest")


def requirement_lines(path: pathlib.Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def distribution_name(requirement: str) -> str:
    """"psycopg[binary]==3.3.6" -> "psycopg". Normalized the way packaging compares names."""
    name = requirement.split("==")[0].split("[")[0]
    return name.strip().lower().replace("_", "-")


def runtime_distributions() -> set[str]:
    return {distribution_name(line) for line in requirement_lines(RUNTIME_REQUIREMENTS)}


def test_every_runtime_pin_matches_the_full_requirements_file() -> None:
    """Verbatim, extras included: the deploy must not resolve a different build of psycopg than the one
    the concurrency tests ran against."""
    everything = set(requirement_lines(ALL_REQUIREMENTS))
    missing = [line for line in requirement_lines(RUNTIME_REQUIREMENTS) if line not in everything]
    assert missing == [], f"pinned differently (or absent) in requirements.txt: {missing}"


def test_the_bundle_carries_nothing_dev_only() -> None:
    bundled = runtime_distributions()
    assert [name for name in DEV_ONLY if name in bundled] == []


def third_party_imports() -> set[str]:
    """Top-level distribution names that `app/` imports, by reading the source rather than trusting a list."""
    modules: set[str] = set()
    for source in sorted((REPO / "app").rglob("*.py")):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])

    known = packages_distributions()
    distributions: set[str] = set()
    for module in modules - {"app"} - set(sys.stdlib_module_names):
        for distribution in known.get(module, []):
            distributions.add(distribution.lower().replace("_", "-"))
    return distributions


def test_everything_app_imports_is_pinned_for_the_lambda() -> None:
    missing = sorted(third_party_imports() - runtime_distributions())
    assert missing == [], f"imported by app/ but not in requirements-lambda.txt: {missing}"


def test_the_email_validator_pydantic_needs_is_pinned() -> None:
    """app/schemas.py uses EmailStr, and pydantic only validates one if email-validator is installed. It
    is an optional extra, so nothing installs it on pydantic's behalf and no import in `app/` names it:
    the scan above cannot see this one, and its absence would turn every checkout into a 500.
    """
    assert "email-validator" in runtime_distributions()


def test_the_handler_terraform_points_at_is_the_one_that_exists() -> None:
    """infra/lambda.tf names the entrypoint as a string. A rename on either side is a deploy that starts
    and then 500s on its first request with an unhelpful import error."""
    configured = (REPO / "infra" / "lambda.tf").read_text()
    assert 'handler          = "app.lambda_handler.handler"' in configured
    assert (REPO / "app" / "lambda_handler.py").is_file()

    import app.lambda_handler as entrypoint

    assert callable(entrypoint.handler)


def test_the_build_script_and_terraform_agree_on_the_architecture() -> None:
    """Compiled wheels for the wrong architecture import fine on this Mac and fail on AWS only."""
    build = (REPO / "scripts" / "build_lambda.sh").read_text()
    configured = (REPO / "infra" / "lambda.tf").read_text()
    assert 'LAMBDA_ARCH="manylinux2014_x86_64"' in build
    assert 'architectures    = ["x86_64"]' in configured
