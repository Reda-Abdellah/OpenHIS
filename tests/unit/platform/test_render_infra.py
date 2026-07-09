"""
Unit tests for infra template rendering (T-09, F#17/F#68/F#78).

Covers:
- render_templates writes the Keycloak realm with .env values
- render_templates writes the consumer-side OIDC config (OpenMRS
  oauth2.properties, OpenELIS extra.properties) from the SAME .env values,
  so realm and consumers can never disagree on a client secret
- StrictUndefined behaviour: a missing variable fails fast, writes nothing
- no committed (leaked) literal secret survives a render with real values
- `opm render-infra --validate` writes nothing
- `opm demo-render` reproduces the well-known dev-only realm + consumer config
- repo hygiene: no rendered secret-bearing artifact is tracked by git
"""
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import infra_render
import opm

REPO_ROOT = Path(__file__).resolve().parents[3]
REALM_TEMPLATE = REPO_ROOT / "infra" / "keycloak" / "openhis-realm.json.j2"

#: Consumer-side OIDC template (relative to infra/) → its secret placeholder.
#: These must carry the SAME variable the realm template uses for that client.
CONSUMER_TEMPLATES = {
    Path("openmrs") / "oauth2.properties.j2": "OPENMRS_KC_CLIENT_SECRET",
    Path("openelis") / "extra.properties.j2": "OPENELIS_OIDC_SECRET",
}

#: Rendered secret-bearing artifacts (relative to infra/) — never git-tracked.
RENDERED_ARTIFACTS = [
    "keycloak/openhis-realm.json",
    "openmrs/oauth2.properties",
    "openelis/extra.properties",
]

#: The client secrets that used to be committed in plaintext.
LEAKED_LITERALS = sorted(infra_render.DEV_DEFAULTS.values())


def _all_output(result) -> str:
    """CliRunner stdout + stderr regardless of the click version's capture mode."""
    output = result.output
    try:
        output += result.stderr
    except (ValueError, AttributeError):
        pass  # mix_stderr=True (click<8.2): stderr is already in .output
    return output


def _context(**overrides) -> dict:
    # Keep underscores so no fake value embeds a hyphenated leaked literal.
    ctx = {
        key: f"Aa1-{key.lower()}-Zz9"
        for key in infra_render.DEV_DEFAULTS
    }
    ctx.update(overrides)
    return ctx


# ── render_templates (module level) ──────────────────────────────────────────

def test_render_writes_realm_with_env_values(tmp_path):
    context = _context()
    outputs = infra_render.render_templates(context, out_root=tmp_path)

    realm = tmp_path / "keycloak" / "openhis-realm.json"
    assert realm in outputs
    assert realm.exists()

    text = realm.read_text()
    data = json.loads(text)  # must stay valid JSON
    for value in context.values():
        assert value in text
    # Keycloak must regenerate signing keys on import — none may be pinned.
    assert "org.keycloak.keys.KeyProvider" not in data.get("components", {})


def test_render_writes_consumer_oidc_files_with_env_values(tmp_path):
    """OpenMRS/OpenELIS OIDC config renders the same secret the realm got."""
    context = _context()
    outputs = infra_render.render_templates(context, out_root=tmp_path)

    openmrs = tmp_path / "openmrs" / "oauth2.properties"
    openelis = tmp_path / "openelis" / "extra.properties"
    assert openmrs in outputs and openmrs.exists()
    assert openelis in outputs and openelis.exists()

    assert f"clientSecret={context['OPENMRS_KC_CLIENT_SECRET']}" in openmrs.read_text()
    assert (
        f"org.itech.login.oauth.clientSecret={context['OPENELIS_OIDC_SECRET']}"
        in openelis.read_text()
    )


def test_no_leaked_literal_survives_render(tmp_path):
    outputs = infra_render.render_templates(_context(), out_root=tmp_path)
    for output in outputs:
        text = output.read_text()
        for leaked in LEAKED_LITERALS:
            assert leaked not in text, (
                f"committed secret '{leaked}' still in rendered {output.name}"
            )


def test_missing_variable_fails_fast_and_writes_nothing(tmp_path):
    context = _context()
    context.pop("HL7_KC_CLIENT_SECRET")

    with pytest.raises(infra_render.InfraRenderError) as excinfo:
        infra_render.render_templates(context, out_root=tmp_path)

    assert "HL7_KC_CLIENT_SECRET" in str(excinfo.value)
    # NOTHING is written — not even templates whose own variables were complete
    for artifact in RENDERED_ARTIFACTS:
        assert not (tmp_path / artifact).exists()


def test_missing_consumer_secret_names_both_realm_and_consumer_template(tmp_path):
    context = _context()
    context.pop("OPENELIS_OIDC_SECRET")

    with pytest.raises(infra_render.InfraRenderError) as excinfo:
        infra_render.render_templates(context, out_root=tmp_path)

    message = str(excinfo.value)
    assert "OPENELIS_OIDC_SECRET" in message
    assert "extra.properties.j2" in message


def test_realm_template_exists_and_uses_placeholders():
    assert REALM_TEMPLATE.exists()
    text = REALM_TEMPLATE.read_text()
    for var in infra_render.DEV_DEFAULTS:
        assert "{{ " + var + " }}" in text, f"template lacks placeholder for {var}"
    for leaked in LEAKED_LITERALS:
        assert leaked not in text, f"template still contains literal '{leaked}'"


@pytest.mark.parametrize(
    "rel_path, placeholder",
    sorted(CONSUMER_TEMPLATES.items()), ids=lambda v: str(v),
)
def test_consumer_template_exists_and_uses_placeholder(rel_path, placeholder):
    template = REPO_ROOT / "infra" / rel_path
    assert template.exists(), f"missing consumer OIDC template {rel_path}"
    text = template.read_text()
    assert "{{ " + placeholder + " }}" in text, (
        f"{rel_path} lacks placeholder for {placeholder}"
    )
    for leaked in LEAKED_LITERALS:
        assert leaked not in text, f"{rel_path} still contains literal '{leaked}'"
    # And the renderer must actually own/discover it
    assert template in infra_render.find_templates()


# ── opm render-infra (CLI) ────────────────────────────────────────────────────

def test_cli_render_infra_with_env_file(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in _context().items()))
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        opm.cli,
        ["render-infra", "--env-file", str(env_file), "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, _all_output(result)
    assert (out_dir / "keycloak" / "openhis-realm.json").exists()
    # Secret values must never reach the console
    for value in _context().values():
        assert value not in _all_output(result)


def test_cli_render_infra_validate_writes_nothing(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in _context().items()))
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        opm.cli,
        ["render-infra", "--env-file", str(env_file),
         "--out-dir", str(out_dir), "--validate"],
    )
    assert result.exit_code == 0, _all_output(result)
    assert not (out_dir / "keycloak" / "openhis-realm.json").exists()


def test_cli_render_infra_missing_var_exits_nonzero(tmp_path):
    context = _context()
    context.pop("RIS_KC_CLIENT_SECRET")
    env_file = tmp_path / "test.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in context.items()))

    runner = CliRunner()
    result = runner.invoke(
        opm.cli,
        ["render-infra", "--env-file", str(env_file),
         "--out-dir", str(tmp_path / "out")],
    )
    assert result.exit_code == 1
    assert "RIS_KC_CLIENT_SECRET" in _all_output(result)


# ── opm demo-render (CLI) ─────────────────────────────────────────────────────

def test_cli_demo_render_produces_dev_realm(tmp_path):
    runner = CliRunner()
    result = runner.invoke(opm.cli, ["demo-render", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, _all_output(result)
    assert "WARNING" in result.output

    realm = tmp_path / "keycloak" / "openhis-realm.json"
    data = json.loads(realm.read_text())
    secrets_by_client = {
        c["clientId"]: c.get("secret") for c in data["clients"] if "secret" in c
    }
    # Dev demo values must line up with the compose ${VAR:-fallback} defaults
    assert secrets_by_client["openhis-platform"] == "openhis-platform-secret"
    assert secrets_by_client["integration-hub-sa"] == "integration-hub-sa-secret"
    assert secrets_by_client["hl7-sa"] == "hl7-sa-secret"


def test_cli_demo_render_consumer_config_matches_dev_realm(tmp_path):
    """Demo-rendered OpenMRS/OpenELIS config carries the historical dev-only
    secrets — the values the demo realm's openmrs / openelis-oidc clients get,
    so SSO still works on a fresh-clone demo stack."""
    runner = CliRunner()
    result = runner.invoke(opm.cli, ["demo-render", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, _all_output(result)

    openmrs = (tmp_path / "openmrs" / "oauth2.properties").read_text()
    assert "clientSecret=openmrs-keycloak-secret" in openmrs

    openelis = (tmp_path / "openelis" / "extra.properties").read_text()
    assert "org.itech.login.oauth.clientSecret=openelis-oidc-secret" in openelis


# ── realm content: SA audience mappers + internal-sync grants ────────────────

#: Every service-account client must stamp aud=openhis-platform into its
#: access tokens, or openhis_sdk audience validation rejects them live
#: (mirrors the mapper tests/e2e/conftest.py adds to harness SA clients).
SA_CLIENTS = [
    "analytics-sa",
    "hl7-sa",
    "integration-hub-sa",
    "orthanc-sa",
    "patient-portal-sa",
    "ris-sa",
]


@pytest.fixture(scope="module")
def dev_realm(tmp_path_factory) -> dict:
    """Realm rendered with the well-known DEV_DEFAULTS, parsed as JSON."""
    out = tmp_path_factory.mktemp("dev-realm")
    infra_render.render_templates(dict(infra_render.DEV_DEFAULTS), out_root=out)
    return json.loads((out / "keycloak" / "openhis-realm.json").read_text())


def _client(realm: dict, client_id: str) -> dict:
    matches = [c for c in realm["clients"] if c["clientId"] == client_id]
    assert len(matches) == 1, f"expected exactly one client {client_id!r}"
    return matches[0]


@pytest.mark.parametrize("client_id", SA_CLIENTS)
def test_sa_client_has_openhis_platform_audience_mapper(dev_realm, client_id):
    client = _client(dev_realm, client_id)
    audience_mappers = [
        m for m in client.get("protocolMappers", [])
        if m.get("protocolMapper") == "oidc-audience-mapper"
    ]
    assert len(audience_mappers) == 1, (
        f"{client_id} needs exactly one oidc-audience-mapper, "
        f"found {len(audience_mappers)}"
    )
    config = audience_mappers[0]["config"]
    assert config["included.custom.audience"] == "openhis-platform"
    assert config["access.token.claim"] == "true"


def test_protocol_mapper_ids_are_unique(dev_realm):
    """Keycloak import silently drops mappers with colliding UUIDs."""
    seen: dict = {}
    for owner in dev_realm["clients"] + dev_realm.get("clientScopes", []):
        owner_name = owner.get("clientId") or owner.get("name")
        for mapper in owner.get("protocolMappers", []) or []:
            mid = mapper.get("id")
            if mid is None:
                continue  # Keycloak assigns one on import
            assert mid not in seen, (
                f"duplicate protocolMapper id {mid}: "
                f"{seen[mid]} and {owner_name}/{mapper.get('name')}"
            )
            seen[mid] = f"{owner_name}/{mapper.get('name')}"


@pytest.mark.parametrize("username, role", [
    # hl7 posts to the hub's internal-sync-gated ingest endpoints
    ("service-account-hl7-sa", "internal-sync"),
    # orthanc plugin posts dicom-stored to the hub (require_roles internal-sync)
    ("service-account-orthanc-sa", "internal-sync"),
    ("service-account-integration-hub-sa", "internal-sync"),
])
def test_service_account_realm_role_grants(dev_realm, username, role):
    users = [u for u in dev_realm["users"] if u["username"] == username]
    assert len(users) == 1, f"expected exactly one user {username!r}"
    assert role in users[0]["realmRoles"], (
        f"{username} lacks realm role {role!r}: {users[0]['realmRoles']}"
    )


# ── repo hygiene ──────────────────────────────────────────────────────────────

def _git(*args) -> "subprocess.CompletedProcess":
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
    )


@pytest.mark.parametrize("artifact", RENDERED_ARTIFACTS)
def test_rendered_artifact_is_not_git_tracked(artifact):
    probe = _git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        pytest.skip("not running inside a git checkout")
    tracked = _git("ls-files", f"infra/{artifact}")
    assert tracked.stdout.strip() == "", (
        f"infra/{artifact} is a rendered secret-bearing "
        "artifact and must not be tracked by git"
    )


@pytest.mark.parametrize("artifact", RENDERED_ARTIFACTS)
def test_gitignore_covers_rendered_artifact(artifact):
    gitignore = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert f"infra/{artifact}" in gitignore
