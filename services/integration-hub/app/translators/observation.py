import datetime, json

def to_fhir_observations_from_ai(job: dict) -> list:
    """Convert AI controller job result_summary → list of FHIR Observations."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    summary = job.get("result_summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            return []
    if not summary:
        return []
    observations = []
    for i, finding in enumerate(summary.get("findings", [])):
        observations.append({
            "resourceType": "Observation",
            "id": f"ai-{job['id'][:8]}-{i}",
            "status": "preliminary",
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                       "code": "imaging"}]}],
            "code": {"text": f"AI Finding: {finding.get('type', 'finding')}"},
            "subject": {"reference": f"Patient/{job.get('patient_id', 'unknown')}"},
            "issued": now,
            "valueString": finding.get("description"),
            "interpretation": [{"text": finding.get("severity", "unknown")}],
            "bodySite": {"text": finding.get("location")},
            "note": [{"text": f"Pipeline: {job.get('pipeline_id')} | Modality: {job.get('modality')}"}],
        })
    return observations
