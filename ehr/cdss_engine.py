"""
Simple rule-based CDSS engine.
Rules are evaluated on order creation and on incoming lab results.
Extend RULES / LAB_RULES to add clinical logic.
"""
import json

ALLERGY_DRUG_MAP = {
    "penicillin":   ["amoxicillin", "ampicillin", "penicillin", "piperacillin"],
    "sulfa":        ["sulfamethoxazole", "trimethoprim-sulfamethoxazole"],
    "nsaid":        ["ibuprofen", "naproxen", "aspirin", "diclofenac"],
    "contrast":     ["iodine", "gadolinium"],
}

CRITICAL_LAB_RULES = [
    {"analyte": "K",            "high": 6.0,  "low": 2.5,  "unit": "mmol/L", "severity": "critical"},
    {"analyte": "Na",           "high": 155,  "low": 120,  "unit": "mmol/L", "severity": "critical"},
    {"analyte": "Glucose",      "high": 27.8, "low": 2.2,  "unit": "mmol/L", "severity": "critical"},
    {"analyte": "Hemoglobin",   "high": None, "low": 6.0,  "unit": "g/dL",   "severity": "critical"},
    {"analyte": "Platelets",    "high": None, "low": 20,   "unit": "10^9/L", "severity": "critical"},
    {"analyte": "Creatinine",   "high": 600,  "low": None, "unit": "µmol/L", "severity": "warning"},
    {"analyte": "Troponin",     "high": 0.04, "low": None, "unit": "µg/L",   "severity": "critical"},
]

def evaluate_order(order: dict) -> list:
    alerts = []
    detail = order.get("order_detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {}
    drug = (detail.get("drug") or detail.get("medication") or "").lower()
    if not drug:
        return alerts
    # Drug-allergy cross-check requires patient allergies — queried at call site
    # Here we check against the order_detail directly
    for allergen_class, drugs in ALLERGY_DRUG_MAP.items():
        if any(d in drug for d in drugs):
            alerts.append({
                "type": "drug-allergy-risk",
                "severity": "warning",
                "message": f"Prescribed drug '{drug}' may belong to the '{allergen_class}' allergy class. Verify patient allergy history."
            })
    return alerts

def evaluate_lab_result(payload: dict) -> list:
    """payload: {ehr_patient_id, order_id, results: [{analyte, value, unit}]}"""
    alerts = []
    for result in payload.get("results", []):
        analyte = result.get("analyte", "")
        try:
            value = float(result.get("value", 0))
        except (TypeError, ValueError):
            continue
        for rule in CRITICAL_LAB_RULES:
            if rule["analyte"].lower() != analyte.lower():
                continue
            if rule["high"] and value > rule["high"]:
                alerts.append({
                    "type": "critical-lab",
                    "severity": rule["severity"],
                    "message": f"CRITICAL HIGH {analyte}: {value} {rule['unit']} (threshold >{rule['high']})"
                })
            elif rule["low"] and value < rule["low"]:
                alerts.append({
                    "type": "critical-lab",
                    "severity": rule["severity"],
                    "message": f"CRITICAL LOW {analyte}: {value} {rule['unit']} (threshold <{rule['low']})"
                })
    return alerts
