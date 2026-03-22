def to_fhir_service_request(order: dict, patient_fhir_id: str) -> dict:
    order_type = order.get("order_type", "")
    category_code = "363679005" if order_type == "IMAGING" else "108252007"  # Imaging / Lab procedure
    category_display = "Imaging" if order_type == "IMAGING" else "Laboratory procedure"
    detail = order.get("order_detail") or {}
    if isinstance(detail, str):
        import json
        try: detail = json.loads(detail)
        except Exception: detail = {}
    return {
        "resourceType": "ServiceRequest",
        "id": str(order.get("id")),
        "status": "active" if order.get("status") == "PENDING" else "completed",
        "intent": "order",
        "category": [{"coding": [{"system": "http://snomed.info/sct",
                                   "code": category_code, "display": category_display}]}],
        "code": {"text": detail.get("test_code") or detail.get("modality") or order_type},
        "subject": {"reference": f"Patient/{patient_fhir_id}"},
        "requester": {"display": order.get("requesting_physician") or "Unknown"},
        "priority": (order.get("priority") or "routine").lower(),
        "note": [{"text": str(detail)}] if detail else [],
    }
