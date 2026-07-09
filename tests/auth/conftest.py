"""
Fixtures for the deny-by-default auth suite (T-04).

Unlike tests/unit and tests/integration (which inherit DEV_MODE=true from
tests/conftest.py), every app booted here runs with DEV_MODE=false and a
dummy KEYCLOAK_URL, so the SDK's real JWT validation path is active. The
JWKS "Keycloak" would serve is injected straight into the SDK's in-memory
cache — no network is involved (see tests/auth/harness.py).

The ``service`` fixture is module-scoped and parametrized over
``harness.SERVICES``: each service app is imported once, all auth probes run
against it, then os.environ / sys.path / sys.modules are restored so suites
running later in the same pytest invocation are unaffected.
"""
import pytest
from fastapi.testclient import TestClient

import harness


@pytest.fixture(scope="module", params=harness.SERVICES, ids=lambda s: s.name)
def service(request, tmp_path_factory):
    """Yield ``(spec, client)`` for every native service with real auth on."""
    spec = request.param
    tmp = str(tmp_path_factory.mktemp(f"auth_{spec.name}"))
    with harness.isolated_service(
        spec.service_dir,
        app_module=spec.app_module,
        env=spec.env(tmp),
        init_db=spec.init_db,
    ) as app:
        yield spec, TestClient(app, raise_server_exceptions=False)
