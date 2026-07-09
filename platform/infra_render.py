"""
infra_render — renders infra/**/*.j2 secret-bearing templates from .env values.

The committed *.j2 templates carry {{ PLACEHOLDER }} variables instead of
real secrets; the rendered siblings (infra/keycloak/openhis-realm.json,
infra/openmrs/oauth2.properties, infra/openelis/extra.properties, …)
are per-deployment artifacts and are git-ignored.  `opm init` and
`opm render-infra` fill the placeholders from .env; `opm demo-render` fills
them with the well-known dev-only values so a fresh clone can still boot
the local demo stack.

nginx/nginx.conf.j2 is deliberately excluded — it is profile-driven and
rendered by nginx_gen.py instead.
"""
from pathlib import Path
from typing import Mapping, Optional

from jinja2 import Environment, StrictUndefined, meta

REPO_ROOT = Path(__file__).parent.parent
INFRA_DIR = REPO_ROOT / "infra"

#: Templates (relative to infra/) that are NOT rendered by this module.
_EXCLUDED = {Path("nginx") / "nginx.conf.j2"}

#: Historical dev-only values used by `opm demo-render`.  They match the
#: docker-compose `${VAR:-fallback}` dev defaults so the demo stack still
#: boots out of the box.  They are public knowledge — NEVER use them
#: outside a throwaway local demo.
DEV_DEFAULTS: dict[str, str] = {
    "ANALYTICS_KC_CLIENT_SECRET": "analytics-sa-secret",
    "HL7_KC_CLIENT_SECRET": "hl7-sa-secret",
    "INTEGRATION_HUB_KC_CLIENT_SECRET": "integration-hub-sa-secret",
    "KEYCLOAK_CLIENT_SECRET": "openhis-platform-secret",
    "ODOO_OIDC_SECRET": "odoo-oidc-secret",
    "OPENELIS_OIDC_SECRET": "openelis-oidc-secret",
    "OPENMRS_KC_CLIENT_SECRET": "openmrs-keycloak-secret",
    "ORTHANC_KC_CLIENT_SECRET": "orthanc-sa-secret",
    "PATIENT_PORTAL_KC_CLIENT_SECRET": "patient-portal-sa-secret",
    "RIS_KC_CLIENT_SECRET": "ris-sa-secret",
}


class InfraRenderError(RuntimeError):
    """A template references variables missing from the render context."""


def _jinja_env() -> Environment:
    return Environment(undefined=StrictUndefined, keep_trailing_newline=True)


def find_templates(infra_dir: Path = INFRA_DIR) -> list[Path]:
    """All *.j2 files under infra/ that this module owns (nginx excluded)."""
    return sorted(
        p for p in infra_dir.rglob("*.j2")
        if p.relative_to(infra_dir) not in _EXCLUDED
    )


def missing_variables(template: Path, context: Mapping[str, str]) -> set[str]:
    """Template variables not provided by *context*."""
    ast = _jinja_env().parse(template.read_text())
    return meta.find_undeclared_variables(ast) - set(context)


def render_templates(
    context: Mapping[str, str],
    infra_dir: Path = INFRA_DIR,
    out_root: Optional[Path] = None,
    write: bool = True,
) -> list[Path]:
    """
    Render every owned infra template with *context*.

    All templates are checked for missing variables BEFORE anything is
    written, so a failure never leaves a partially rendered set behind.

    Args:
        context:   variable name → value (typically parsed from .env).
        infra_dir: template root (overridable for tests).
        out_root:  write rendered files under this root instead of next to
                   the templates (used by `--out-dir` and `opm init --output-dir`).
        write:     when False, only validate; nothing is written.

    Returns:
        The output paths (written, or planned when write=False).

    Raises:
        InfraRenderError: if any template needs a variable absent from context.
    """
    templates = find_templates(infra_dir)

    problems: list[str] = []
    for template in templates:
        missing = missing_variables(template, context)
        if missing:
            rel = template.relative_to(infra_dir)
            problems.append(f"{rel}: missing {', '.join(sorted(missing))}")
    if problems:
        raise InfraRenderError(
            "cannot render infra templates — variables missing from the "
            "environment (add them to .env or re-run `opm init`):\n"
            + "\n".join(f"    • {p}" for p in problems)
        )

    env = _jinja_env()
    outputs: list[Path] = []
    for template in templates:
        rendered = env.from_string(template.read_text()).render(**context)
        target_root = out_root if out_root is not None else infra_dir
        target = (target_root / template.relative_to(infra_dir)).with_suffix("")
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered)
        outputs.append(target)
    return outputs
