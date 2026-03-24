def to_fhir_medication_request(rx: dict) -> dict:
    """Convert pharmacy prescription to FHIR R4 MedicationRequest."""
    dosage_text = f"{rx.get('dose','')} {rx.get('route','oral')} {rx.get('frequency','')}".strip()
    resource = {
        "resourceType": "MedicationRequest",
        "id":           f"rx-{rx.get('id', 'unknown')}",
        "status":       "active" if rx.get("status") in ("pending", "verified") else "completed",
        "intent":       "order",
        "medicationCodeableConcept": {
            "text": rx.get("drug_name", "")
        },
        "subject": {
            "reference": f"Patient/{rx.get('ehr_patient_id', 'unknown')}"
        },
        "requester": {
            "display": rx.get("prescriber") or "Unknown"
        },
        "dosageInstruction": [{
            "text":  dosage_text,
            "route": {"text": rx.get("route", "oral")},
            "timing": {
                "code": {"text": rx.get("frequency", "")}
            }
        }],
    }
    if rx.get("duration_days"):
        resource["dispenseRequest"] = {
            "expectedSupplyDuration": {
                "value": rx["duration_days"],
                "unit": "days",
                "system": "http://unitsofmeasure.org",
                "code": "d"
            }
        }
    if rx.get("quantity"):
        resource.setdefault("dispenseRequest", {})["quantity"] = {
            "value": rx["quantity"]
        }
    return resource
