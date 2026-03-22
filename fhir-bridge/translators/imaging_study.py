import datetime

def to_fhir_imaging_study(orthanc_meta: dict, patient_fhir_id: str) -> dict:
    tags = orthanc_meta.get("MainDicomTags", {})
    study_tags = orthanc_meta.get("StudyMainDicomTags", {})
    now = datetime.datetime.utcnow().isoformat() + "Z"
    return {
        "resourceType": "ImagingStudy",
        "id": study_tags.get("StudyInstanceUID", orthanc_meta.get("ParentStudy", "unknown")),
        "status": "available",
        "subject": {"reference": f"Patient/{patient_fhir_id}"},
        "started": now,
        "modality": [{"system": "http://dicom.nema.org/resources/ontology/DCM",
                      "code": tags.get("Modality", "")}],
        "description": tags.get("SeriesDescription") or study_tags.get("StudyDescription"),
        "series": [{
            "uid": tags.get("SeriesInstanceUID"),
            "modality": {"system": "http://dicom.nema.org/resources/ontology/DCM",
                         "code": tags.get("Modality", "")},
            "description": tags.get("SeriesDescription"),
            "numberOfInstances": len(orthanc_meta.get("Instances", [])),
        }],
    }
