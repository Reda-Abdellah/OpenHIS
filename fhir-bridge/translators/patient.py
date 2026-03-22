import uuid

def to_fhir_patient(ehr_patient: dict) -> dict:
    return {
        "resourceType": "Patient",
        "id": ehr_patient.get("id", str(uuid.uuid4())),
        "identifier": [{"system": "urn:oid:2.16.840.1.113883.4.6",
                         "value": ehr_patient.get("mrn", "")}],
        "name": [{"family": ehr_patient.get("last_name", ""),
                  "given":  [ehr_patient.get("first_name", "")]}],
        "birthDate": ehr_patient.get("birth_date"),
        "gender":    (ehr_patient.get("sex") or "unknown").lower(),
        "telecom":   [{"system": "phone", "value": ehr_patient["phone"]}]
                     if ehr_patient.get("phone") else [],
    }
