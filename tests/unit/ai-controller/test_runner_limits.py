"""
Container hardening in runner.py (T-02):

  * _run_container_sync refuses images outside POC_ALLOWED_IMAGES,
  * pipeline containers get hard resource limits + dropped capabilities,
  * container logs are bounded (tail=1000),
  * _prepare_input/_prepare_clinical_input reject path-traversal job ids.

The docker client is fully mocked — no daemon, no network.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


def _insert_pipeline_and_job(docker_image: str) -> str:
    """Create a pipeline with the given image plus one PENDING job for it."""
    from database import get_db

    pipeline_id = f"limits-{uuid.uuid4().hex[:8]}"
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO pipelines (id,name,docker_image) VALUES (?,?,?)",
            (pipeline_id, "Limits Test", docker_image),
        )
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,status,"
            "trigger_type,created_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, pipeline_id, "", "", "PENDING", "MANUAL", now),
        )
    return job_id


# ── image allowlist ───────────────────────────────────────────────────────────

def test_non_allowlisted_image_raises_and_never_runs(fresh_db):
    import runner

    job_id = _insert_pipeline_and_job("evil/cryptominer:latest")
    client = MagicMock()
    with patch.object(runner.docker, "from_env", return_value=client):
        with pytest.raises(RuntimeError, match="not in allowlist"):
            runner._run_container_sync(job_id)
    client.containers.run.assert_not_called()


def test_default_allowlist_contains_poc_images(fresh_db):
    """Default allowlist must cover all four seeded pipelines (database.py),
    including the bus-triggered clinical POCs — otherwise lab_result.ready /
    patient.synced auto-triggered jobs fail at the runner."""
    import runner

    assert "openhis/poc-xray:latest" in runner.POC_ALLOWED_IMAGES
    assert "openhis/poc-ct:latest" in runner.POC_ALLOWED_IMAGES
    assert "openhis/poc-lab-risk:latest" in runner.POC_ALLOWED_IMAGES
    assert "openhis/poc-emr-alert:latest" in runner.POC_ALLOWED_IMAGES


# ── resource limits + bounded logs ────────────────────────────────────────────

def test_allowlisted_image_runs_with_resource_limits(fresh_db):
    import runner

    job_id = _insert_pipeline_and_job("openhis/poc-xray:latest")

    container = MagicMock()
    container.id = "cid-123"
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"pipeline ok"
    client = MagicMock()
    client.containers.run.return_value = container

    with patch.object(runner.docker, "from_env", return_value=client):
        cid, logs, code = runner._run_container_sync(job_id)

    assert (cid, logs, code) == ("cid-123", "pipeline ok", 0)

    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["image"] == "openhis/poc-xray:latest"
    assert kwargs["mem_limit"] == runner.POC_MEM_LIMIT
    assert kwargs["memswap_limit"] == runner.POC_MEM_LIMIT
    assert kwargs["pids_limit"] == runner.POC_PIDS_LIMIT
    assert kwargs["nano_cpus"] == int(runner.POC_CPU_LIMIT * 1e9)
    assert kwargs["security_opt"] == ["no-new-privileges"]
    assert kwargs["cap_drop"] == ["ALL"]

    # Logs are bounded and the container is always removed.
    container.logs.assert_called_once_with(tail=runner.CONTAINER_LOG_TAIL)
    container.remove.assert_called_once_with(force=True)


# ── job_id path-traversal validation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_clinical_input_rejects_traversal_job_id(fresh_db, tmp_path):
    import runner

    runner.JOBS_DATA_DIR = str(tmp_path)
    with pytest.raises(RuntimeError, match="invalid job_id"):
        await runner._prepare_clinical_input("../../etc/cron.d/evil")


@pytest.mark.asyncio
async def test_prepare_input_rejects_traversal_job_id(fresh_db, tmp_path):
    import runner

    runner.JOBS_DATA_DIR = str(tmp_path)
    with pytest.raises(RuntimeError, match="invalid job_id"):
        await runner._prepare_input("../escape")


def test_run_container_sync_rejects_traversal_job_id(fresh_db):
    import runner

    with pytest.raises(RuntimeError, match="invalid job_id"):
        runner._run_container_sync("../escape")


def test_uuid_job_ids_pass_validation():
    import runner

    assert runner._validate_job_id(str(uuid.uuid4()))
