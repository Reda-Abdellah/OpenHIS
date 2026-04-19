"""
Scenario 6 — Resilience & Recovery

Mirrors SCENARIO 6 in docs/verification_and_validation/v-and-v-scenario.md.

These tests require docker socket access (so the test process can `docker
compose stop/start <svc>`). If docker is not reachable without sudo, the
entire class is skipped with a clear message — add the running user to the
`docker` group or run these tests as root to execute them.

Covers:
  ✅ S6.1 — Redis persistence: AOF is enabled (events survive Redis restart)
  ✅ S6.2 — integration-hub restart: audit log persists across the restart
  ✅ S6.3 — MPI restart: master-patient rows persist across the restart
"""
import subprocess
import time

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.resilience]


def _docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _wait_healthy(url_fn, timeout: float = 60.0) -> bool:
    """Poll a 0-arg callable returning a requests.Response until HTTP 200."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = url_fn()
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


class TestS6_Resilience:

    @pytest.fixture(autouse=True)
    def _guard(self, docker_available):
        if not docker_available:
            pytest.skip(
                "docker CLI not reachable without sudo — add your user to the "
                "`docker` group or run the suite as root to exercise S6."
            )

    def test_s6_1_redis_aof_enabled(self):
        """
        Redis should be started with append-only persistence so the event
        stream survives container restarts. OBJ 3.3 in earlier planning.
        """
        # Find the redis container by label/name (depends on compose layout).
        r = _docker("ps", "--filter", "name=redis", "--format", "{{.Names}}")
        names = [n.strip() for n in r.stdout.splitlines() if n.strip()]
        if not names:
            pytest.skip("no running redis container found")
        container = names[0]
        r = _docker("exec", container, "redis-cli", "CONFIG", "GET", "appendonly")
        assert r.returncode == 0, r.stderr
        # Output format: "appendonly\nyes\n"
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        assert len(lines) >= 2
        assert lines[1].lower() == "yes", (
            f"Redis AOF is '{lines[1]}' — event stream will NOT survive a "
            "Redis restart. See OBJ 3.3."
        )

    def test_s6_2_integration_hub_restart_preserves_audit(self, hub_api):
        """Restart integration-hub and confirm its audit log persists."""
        r_before = hub_api.get("/audit", params={"limit": 200})
        assert r_before.status_code == 200
        count_before = r_before.json().get("count", 0)

        # Locate container
        r = _docker("ps", "--filter", "name=integration-hub", "--format", "{{.Names}}")
        names = [n.strip() for n in r.stdout.splitlines() if n.strip()]
        if not names:
            pytest.skip("no running integration-hub container found")
        container = names[0]

        _docker("restart", container, timeout=45)
        # Wait for the service to answer on /api/health again
        ok = _wait_healthy(lambda: hub_api.get("/health"), timeout=60)
        assert ok, "integration-hub did not return to health within 60s"

        r_after = hub_api.get("/audit", params={"limit": 200})
        assert r_after.status_code == 200
        count_after = r_after.json().get("count", 0)
        assert count_after >= count_before, (
            f"audit shrank from {count_before} to {count_after} — audit log "
            "is not persisted across restarts"
        )

    def test_s6_3_mpi_restart_preserves_master_patients(self, mpi_api, fresh_mrn):
        """Create a patient, restart MPI, confirm the patient is still there."""
        create = mpi_api.post("/patients", json={
            "mrn":       fresh_mrn,
            "firstname": "Persistence",
            "lastname":  "Test",
        })
        assert create.status_code == 201
        pid = create.json()["id"]

        r = _docker("ps", "--filter", "name=mpi", "--format", "{{.Names}}")
        names = [n.strip() for n in r.stdout.splitlines() if n.strip() and "mpi" in n]
        if not names:
            pytest.skip("no running mpi container found")
        container = names[0]

        _docker("restart", container, timeout=45)
        ok = _wait_healthy(lambda: mpi_api.get("/health"), timeout=60)
        assert ok, "mpi did not return to health within 60s"

        after = mpi_api.get(f"/patients/{pid}")
        assert after.status_code == 200, after.text
        assert after.json()["mrn"] == fresh_mrn
