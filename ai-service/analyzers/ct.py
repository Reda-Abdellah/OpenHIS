"""CT analyzer — chest / head / abdomen."""

import hashlib, random
from typing import Tuple


def _seeded(iid: str) -> random.Random:
    return random.Random(int(hashlib.md5(iid.encode()).hexdigest()[:8], 16))


_CHEST_POOL = [
    {"type":"normal",    "description":"No acute intrathoracic process",
     "location":"bilateral","severity":"none","conf_range":(.93,.99),"measurements":{}},
    {"type":"nodule",    "description":"Pulmonary nodule — right upper lobe",
     "location":"RUL",     "severity":"low", "conf_range":(.64,.88),
     "measurements":{"diameter_mm":None}},
    {"type":"ground_glass","description":"Ground-glass opacity — left lower lobe",
     "location":"LLL",     "severity":"moderate","conf_range":(.70,.90),
     "measurements":{"area_cm2":None}},
    {"type":"effusion",  "description":"Small bilateral pleural effusions",
     "location":"bilateral","severity":"mild","conf_range":(.78,.95),"measurements":{}},
    {"type":"lymph_node","description":"Enlarged mediastinal lymph node",
     "location":"mediastinum","severity":"moderate","conf_range":(.65,.83),
     "measurements":{"short_axis_mm":None}},
    {"type":"emphysema", "description":"Centrilobular emphysema — upper lobes",
     "location":"bilateral","severity":"mild","conf_range":(.80,.95),"measurements":{}},
    {"type":"pe",        "description":"Filling defect — possible pulmonary embolism",
     "location":"right PA","severity":"high","conf_range":(.70,.88),"measurements":{}},
]
_HEAD_POOL = [
    {"type":"normal",      "description":"No acute intracranial abnormality",
     "location":"global",  "severity":"none","conf_range":(.92,.99),"measurements":{}},
    {"type":"hyperdensity","description":"Hyperdense focus — possible hemorrhage",
     "location":"right BG","severity":"high","conf_range":(.68,.87),
     "measurements":{"volume_mL":None}},
    {"type":"hypodensity", "description":"Hypodense region — possible ischemia",
     "location":"left MCA","severity":"high","conf_range":(.65,.84),
     "measurements":{"area_cm2":None}},
    {"type":"mass_effect", "description":"Midline shift",
     "location":"midline", "severity":"high","conf_range":(.72,.90),
     "measurements":{"shift_mm":None}},
    {"type":"atrophy",    "description":"Mild cortical atrophy",
     "location":"global",  "severity":"mild","conf_range":(.82,.96),"measurements":{}},
]
_ABDOMEN_POOL = [
    {"type":"normal",  "description":"No acute abdominal abnormality",
     "location":"global","severity":"none","conf_range":(.90,.98),"measurements":{}},
    {"type":"lesion",  "description":"Liver hypodense lesion — possible cyst",
     "location":"liver","severity":"low","conf_range":(.72,.91),
     "measurements":{"diameter_mm":None}},
    {"type":"stone",   "description":"Right renal calculus",
     "location":"right kidney","severity":"moderate","conf_range":(.80,.96),
     "measurements":{"diameter_mm":None}},
    {"type":"appendix","description":"Dilated appendix — possible appendicitis",
     "location":"RIF",  "severity":"high","conf_range":(.73,.90),
     "measurements":{"diameter_mm":None}},
    {"type":"lymph_node","description":"Retroperitoneal lymphadenopathy",
     "location":"retroperitoneum","severity":"moderate","conf_range":(.68,.85),
     "measurements":{"short_axis_mm":None}},
]

_MEAS_DEFAULTS = {"diameter_mm": (4, 25), "area_cm2": (0.8, 8.0),
                  "volume_mL": (1.0, 15.0), "shift_mm": (2, 10),
                  "short_axis_mm": (10, 22)}


def _pick_findings(pool, rng):
    normal_only = rng.random() < 0.65
    chosen = [pool[0]] if normal_only else         [pool[0]] + rng.sample(pool[1:], min(rng.randint(1, 2), len(pool)-1))

    findings = []
    for idx, f in enumerate(chosen):
        lo, hi = f["conf_range"]
        m = {}
        for k, v in f["measurements"].items():
            if v is None:
                lo_m, hi_m = _MEAS_DEFAULTS.get(k, (1, 10))
                m[k] = round(rng.uniform(lo_m, hi_m), 1)
            else:
                m[k] = v
        findings.append({
            "id": f"f{idx+1}", "type": f["type"],
            "description": f["description"], "location": f["location"],
            "confidence": round(rng.uniform(lo, hi), 2),
            "severity": f["severity"], "measurements": m,
        })
    return findings


def run(ds, instance_id: str) -> Tuple[list, str, bool, bool]:
    rng       = _seeded(instance_id)
    body_part = str(ds.get("BodyPartExamined", "CHEST")).upper()

    pool = (_HEAD_POOL    if "HEAD" in body_part or "BRAIN" in body_part else
            _ABDOMEN_POOL if "ABD"  in body_part or "PELV" in body_part else
            _CHEST_POOL)

    findings  = _pick_findings(pool, rng)
    normal    = all(f["severity"] == "none" for f in findings)
    follow_up = not normal
    critical  = any(f["severity"] == "high" for f in findings)

    if normal:
        impression = "No acute findings identified on CT. Normal study."
    elif critical:
        desc = "; ".join(f["description"] for f in findings if f["severity"] not in ("none","mild"))
        impression = f"CRITICAL: {desc}. Urgent clinical review required."
    else:
        desc = "; ".join(f["description"] for f in findings if f["severity"] != "none")
        impression = f"Findings: {desc}. Clinical correlation recommended."

    return findings, impression, normal, follow_up
