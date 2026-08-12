"""Regression tests for install-time runtime dependency declarations."""

from pathlib import Path
import tomllib

from packaging.requirements import Requirement


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_GOOGLE_ADK_VERSION = "2.6.3"


def _find_google_adk_requirement(requirements: list[str]) -> Requirement:
    """Return the declared Google ADK requirement from a dependency list."""
    for value in requirements:
        requirement = Requirement(value)
        if requirement.name == "google-adk":
            return requirement
    raise AssertionError("google-adk must be declared as a runtime dependency")


def test_google_adk_database_extra_is_declared_for_runtime_installers() -> None:
    """Ensure every supported installer includes ADK database dependencies."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)

    requirements_lines = [
        line.strip()
        for line in (PROJECT_ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    dependency_sources = {
        "pyproject.toml": pyproject["project"]["dependencies"],
        "requirements.txt": requirements_lines,
    }

    for source, requirements in dependency_sources.items():
        google_adk = _find_google_adk_requirement(requirements)
        assert "db" in google_adk.extras, (
            f"{source} must declare the google-adk db extra because the runtime "
            "imports DatabaseSessionService"
        )
        assert str(google_adk.specifier) == f"=={SUPPORTED_GOOGLE_ADK_VERSION}", (
            f"{source} must pin the exact Google ADK version validated by the runtime"
        )

    eval_adk = _find_google_adk_requirement(pyproject["project"]["optional-dependencies"]["eval"])
    assert "eval" in eval_adk.extras
    assert str(eval_adk.specifier) == f"=={SUPPORTED_GOOGLE_ADK_VERSION}"
