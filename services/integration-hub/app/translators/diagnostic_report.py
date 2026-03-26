import datetime

def to_fhir_diagnostic_report_lab(payload: dict) -> dict:
    """Convert LIS final result payload → FHIR DiagnosticReport."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    observations = []
    for i, r in enumerate(payload.get("results", [])):
        observations.append({
            "resourceType": "Observation",
            "id": f"obs-{payload.get('order_id')}-{i}",
            "status": "final",
            "code": {"text": r.get("analyte")},
            "subject": {"reference": f"Patient/{payload.get('ehr_patient_id')}"},
            "valueQuantity": {
                "value": _try_float(r.get("value")),
                "unit":  r.get("unit", ""),
                "system": "http://unitsofmeasure.org",
            },
            "interpretation": [{"text": r.get("flag", "normal")}],
            "referenceRange": [{"text": r.get("reference_range")}] if r.get("reference_range") else [],
        })
    return {
        "resourceType": "DiagnosticReport",
        "id": f"lab-{payload.get('order_id')}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                   "code": "LAB", "display": "Laboratory"}]}],
        "code": {"text": payload.get("test_code", "Lab Result")},
        "subject": {"reference": f"Patient/{payload.get('ehr_patient_id')}"},
        "issued": now,
        "result": [{"reference": f"#{o['id']}"} for o in observations],
        "contained": observations,
    }

def to_fhir_diagnostic_report_radiology(payload: dict) -> dict:
    """Convert RIS final radiology report → FHIR DiagnosticReport."""
    now = datetime.datetime.utcnow().isoformat() + "Z"
    return {
        "resourceType": "DiagnosticReport",
        "id": f"rad-{payload.get('report_id')}",
        "status": "final",
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                                   "code": "RAD", "display": "Radiology"}]}],
        "code": {"text": f"{payload.get('modality', 'Radiology')} Report"},
        "subject": {"reference": f"Patient/{payload.get('ehr_patient_id', 'unknown')}"},
        "issued": now,
        "conclusion": payload.get("impression"),
        "presentedForm": [{"contentType": "text/plain",
                           "data": _b64(payload.get("findings") or "")}],
    }

def _try_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()
