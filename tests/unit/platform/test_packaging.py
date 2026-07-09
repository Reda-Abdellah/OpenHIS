"""
Packaging metadata tests for the OPM CLI (platform/pyproject.toml).

Covers:
- PyPI-safe project name (openhis-opm) while the console command stays `opm`
- version single-sourced from opm.__version__ via hatch dynamic version
- console_script entry point resolves to a real callable (opm:cli)
- runtime dependencies match what opm.py actually imports (no rich)
- `opm --version` prints the package version and exits 0
"""
import re
import tomllib
from pathlib import Path

from click.testing import CliRunner

import opm

PYPROJECT = Path(__file__).resolve().parents[3] / "platform" / "pyproject.toml"


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


# ── project metadata ──────────────────────────────────────────────────────────

def test_project_name_is_pypi_safe():
    project = _load_pyproject()["project"]
    assert project["name"] == "openhis-opm"


def test_version_is_single_sourced_from_opm_module():
    data = _load_pyproject()
    project = data["project"]
    # No static version — hatch reads __version__ from opm.py.
    assert "version" not in project, "version must be dynamic, not duplicated"
    assert "version" in project.get("dynamic", [])
    assert data["tool"]["hatch"]["version"]["path"] == "opm.py"
    # And the module actually exposes a semver-ish __version__.
    assert re.fullmatch(r"\d+\.\d+\.\d+([.\-+].*)?", opm.__version__)


def test_console_script_points_at_real_callable():
    scripts = _load_pyproject()["project"]["scripts"]
    assert scripts["opm"] == "opm:cli"
    module_name, _, attr = scripts["opm"].partition(":")
    assert module_name == "opm"
    assert callable(getattr(opm, attr)), f"opm.{attr} is not callable"


def test_dependencies_match_actual_imports():
    deps = _load_pyproject()["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[; ]", d, 1)[0].lower() for d in deps}
    assert {"click", "pyyaml", "jinja2", "requests"} <= names
    assert "rich" not in names, "rich is not imported anywhere in platform/"


def test_required_metadata_present():
    project = _load_pyproject()["project"]
    assert project["readme"] == "README.md"
    assert (PYPROJECT.parent / "README.md").is_file()
    assert "Apache-2.0" in str(project["license"])
    assert project["requires-python"] == ">=3.11"
    assert project.get("urls"), "PyPI metadata needs [project.urls]"
    assert project.get("classifiers"), "PyPI metadata needs classifiers"


# ── opm --version ─────────────────────────────────────────────────────────────

def test_opm_version_flag_prints_version():
    result = CliRunner().invoke(opm.cli, ["--version"])
    assert result.exit_code == 0, result.output
    assert opm.__version__ in result.output
    assert "opm" in result.output


def test_opm_version_flag_does_not_require_a_stack():
    """--version must short-circuit before any compose/.env/admin-API access."""
    result = CliRunner().invoke(opm.cli, ["--version"])
    assert result.exit_code == 0
    assert "ERROR" not in result.output
