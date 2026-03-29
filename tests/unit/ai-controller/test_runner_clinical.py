"""
Tests for the clinical (non-imaging) branch of runner.py.

Uses tmp_path for JOBS_DATA_DIR and mocks orthanc_client + docker to ensure
no network calls are made.
"""
import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


# ── _prepare_clinical_input ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_clinical_input_writes_input_json(fresh_db, tmp_path):
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    os.environ["JOBS_DATA_DIR"] = str(tmp_path)
    runner.JOBS_DATA_DIR = str(tmp_path)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,event_payload,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "",
             "lab_result", "dr-001", '{"oe_id":"dr-001","subject":"Patient/p1"}',
             "PENDING", "AUTO", now),
        )

    await runner._prepare_clinical_input(job_id)

    input_json = tmp_path / job_id / "input" / "input.json"
    assert input_json.exists(), "input.json was not created"
    data = json.loads(input_json.read_text())
    assert data["job_id"] == job_id
    assert data["pipeline_id"] == "poc-lab-risk"
    assert data["source_type"] == "lab_result"
    assert data["event_source_id"] == "dr-001"
    assert "payload" in data


@pytest.mark.asyncio
async def test_prepare_clinical_input_registers_json_payload_artifact(fresh_db, tmp_path):
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,event_payload,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result", "dr-002", "{}", "PENDING", "AUTO", now),
        )

    await runner._prepare_clinical_input(job_id)

    with get_db() as db:
        arts = db.execute(
            "SELECT * FROM artifacts WHERE job_id=? AND artifact_type='json_payload'",
            (job_id,),
        ).fetchall()
    assert len(arts) == 1
    assert arts[0]["direction"] == "input"
    assert arts[0]["filename"] == "input.json"


@pytest.mark.asyncio
async def test_prepare_clinical_input_never_calls_orthanc(fresh_db, tmp_path):
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,"
            "source_type,event_source_id,event_payload,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result", "dr-003", "{}", "PENDING", "AUTO", now),
        )

    with patch("orthanc_client.get_series_metadata", new_callable=AsyncMock) as mock_oc:
        await runner._prepare_clinical_input(job_id)
    mock_oc.assert_not_called()


# ── _process_output with risk_score ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_output_extracts_risk_score(fresh_db, tmp_path):
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "RUNNING", "AUTO", now),
        )

    # Create the output directory and result.json
    output_dir = tmp_path / job_id / "output"
    output_dir.mkdir(parents=True)
    result = {
        "pipeline_id": "poc-lab-risk",
        "job_id": job_id,
        "normal": True,
        "critical": False,
        "risk_score": 0.23,
        "findings": ["Mild anaemia"],
        "impression": "Low risk",
        "output_files": [],
    }
    (output_dir / "result.json").write_text(json.dumps(result))

    await runner._process_output(job_id)

    with get_db() as db:
        row = db.execute("SELECT result_summary FROM jobs WHERE id=?", (job_id,)).fetchone()
    summary = json.loads(row["result_summary"])
    assert summary["risk_score"] == 0.23
    assert summary["normal"] is True
    assert summary["findings_count"] == 1


@pytest.mark.asyncio
async def test_process_output_risk_score_none_when_absent(fresh_db, tmp_path):
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, "poc-xray", "", "", "RUNNING", "AUTO", now),
        )
    output_dir = tmp_path / job_id / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "result.json").write_text(json.dumps(
        {"normal": True, "critical": False, "findings": [], "impression": "Normal"}
    ))

    await runner._process_output(job_id)

    with get_db() as db:
        row = db.execute("SELECT result_summary FROM jobs WHERE id=?", (job_id,)).fetchone()
    summary = json.loads(row["result_summary"])
    assert summary["risk_score"] is None


# ── run_job dispatch ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_job_imaging_calls_prepare_input(fresh_db, tmp_path):
    """For source_type=imaging, _prepare_input is called (not _prepare_clinical_input)."""
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,source_type,"
            "orthanc_series_id,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-xray", "uid-1", "uid-2", "imaging",
             "orthanc-s-001", "PENDING", "MANUAL", now),
        )

    with patch.object(runner, "_prepare_input", new_callable=AsyncMock) as mock_prep, \
         patch.object(runner, "_prepare_clinical_input", new_callable=AsyncMock) as mock_clin:
        # _prepare_input raises so run_job will FAIL — that's fine for this assertion
        mock_prep.side_effect = RuntimeError("test stop")
        await runner.run_job(job_id)

    mock_prep.assert_called_once_with(job_id)
    mock_clin.assert_not_called()


@pytest.mark.asyncio
async def test_run_job_clinical_calls_prepare_clinical_input(fresh_db, tmp_path):
    """For source_type=lab_result, _prepare_clinical_input is called."""
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,source_type,"
            "event_source_id,event_payload,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result",
             "dr-010", "{}", "PENDING", "AUTO", now),
        )

    with patch.object(runner, "_prepare_input", new_callable=AsyncMock) as mock_prep, \
         patch.object(runner, "_prepare_clinical_input", new_callable=AsyncMock) as mock_clin:
        mock_clin.side_effect = RuntimeError("test stop")
        await runner.run_job(job_id)

    mock_clin.assert_called_once_with(job_id)
    mock_prep.assert_not_called()


@pytest.mark.asyncio
async def test_run_job_clinical_skips_saveback(fresh_db, tmp_path):
    """Clinical jobs do not trigger saveback even if a rule would allow it."""
    import runner
    from database import get_db
    from datetime import datetime, timezone
    import uuid

    runner.JOBS_DATA_DIR = str(tmp_path)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute(
            "INSERT INTO jobs (id,pipeline_id,series_uid,study_uid,source_type,"
            "event_source_id,event_payload,status,trigger_type,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, "poc-lab-risk", "", "", "lab_result",
             "dr-011", "{}", "PENDING", "AUTO", now),
        )

    with patch.object(runner, "_prepare_clinical_input", new_callable=AsyncMock), \
         patch.object(runner, "_run_container_sync", return_value=("cid", "ok", 0)), \
         patch.object(runner, "_process_output", new_callable=AsyncMock), \
         patch.object(runner, "_maybe_auto_saveback", new_callable=AsyncMock) as mock_sb:
        await runner.run_job(job_id)

    mock_sb.assert_not_called()
