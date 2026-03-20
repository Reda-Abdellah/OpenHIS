"""Analyzer dispatch — modality-specific modules added in Step 4.2."""

import io
import time
import pydicom


def analyze(dicom_bytes: bytes, instance_id: str) -> dict:
    """
    Entry point called by main.py for every new instance.
    Returns a structured result dict. Modality-specific logic
    lives in the sub-modules imported below.
    """
    t0 = time.time()

    ds       = pydicom.dcmread(io.BytesIO(dicom_bytes))
    modality  = str(ds.get("Modality",         "?"))
    body_part = str(ds.get("BodyPartExamined",  "")).upper()
    patient   = str(ds.get("PatientName",       "UNKNOWN"))
    study_uid = str(ds.get("StudyInstanceUID",  ""))
    series_uid= str(ds.get("SeriesInstanceUID", ""))

    # ── dispatch ──────────────────────────────────────────────────────────────
    findings   = []
    impression = "Analysis complete. No specific findings detected."
    normal     = True
    follow_up  = False

    # modality-specific analyzers will be imported in step 4.2
    # For now we return a well-structured "normal" result
    try:
        if modality in ("CR", "DX"):
            from . import xray
            findings, impression, normal, follow_up = xray.run(ds, instance_id)
        elif modality == "CT":
            from . import ct
            findings, impression, normal, follow_up = ct.run(ds, instance_id)
        elif modality == "MR":
            from . import mr
            findings, impression, normal, follow_up = mr.run(ds, instance_id)
        elif modality == "US":
            from . import us
            findings, impression, normal, follow_up = us.run(ds, instance_id)
    except ImportError:
        pass  # analyzer module not yet installed — return skeleton

    return {
        "instance_id"        : instance_id,
        "status"             : "completed",
        "modality"           : modality,
        "body_part"          : body_part,
        "patient_name"       : patient,
        "study_instance_uid" : study_uid,
        "series_instance_uid": series_uid,
        "analysis_ms"        : round((time.time() - t0) * 1000),
        "findings"           : findings,
        "impression"         : impression,
        "normal"             : normal,
        "follow_up_recommended": follow_up,
    }
