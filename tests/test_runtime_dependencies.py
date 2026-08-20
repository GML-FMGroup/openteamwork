"""Regression tests for install-time runtime dependency declarations."""

from pathlib import Path
import tomllib

from packaging.requirements import Requirement


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_GOOGLE_ADK_VERSION = "2.6.3"
LEGACY_OFFICE_DEPENDENCIES = {
    "office-oxide": "==0.1.8",
    "olefile": "==0.47",
}


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


def test_legacy_office_parsers_are_exactly_pinned_for_runtime_installers() -> None:
    """Keep the validated native parser and OLE preflight versions reproducible."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    requirements_lines = [
        line.strip()
        for line in (PROJECT_ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    for source, values in {
        "pyproject.toml": pyproject["project"]["dependencies"],
        "requirements.txt": requirements_lines,
    }.items():
        requirements = {Requirement(value).name: Requirement(value) for value in values}
        for name, expected_specifier in LEGACY_OFFICE_DEPENDENCIES.items():
            assert name in requirements, f"{source} must declare {name}"
            assert str(requirements[name].specifier) == expected_specifier
