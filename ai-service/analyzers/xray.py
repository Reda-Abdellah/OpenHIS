"""CR / DX chest X-ray synthetic analyzer."""

import hashlib
import random
from typing import Tuple


def _seeded(instance_id: str, salt: str = "") -> random.Random:
    seed = int(hashlib.md5((instance_id + salt).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def run(ds, instance_id: str) -> Tuple[list, str, bool, bool]:
    rng       = _seeded(instance_id)
    body_part = str(ds.get("BodyPartExamined", "CHEST")).upper()
    is_chest  = body_part in ("CHEST", "THORAX", "")

    # ── pool of possible findings per anatomy ──────────────────────────────
    chest_pool = [
        {"type": "normal",      "description": "No acute cardiopulmonary process",
         "location": "bilateral", "severity": "none", "conf_range": (.92, .99),
         "measurements": {}},
        {"type": "opacity",     "description": "Right lower lobe opacity",
         "location": "RLL",       "severity": "moderate", "conf_range": (.72, .91),
         "measurements": {"area_cm2": round(rng.uniform(2.0, 6.5), 1)}},
        {"type": "opacity",     "description": "Left lower lobe consolidation",
         "location": "LLL",       "severity": "moderate", "conf_range": (.68, .88),
         "measurements": {"area_cm2": round(rng.uniform(1.5, 5.0), 1)}},
        {"type": "nodule",      "description": "Pulmonary nodule — right upper lobe",
         "location": "RUL",       "severity": "low",      "conf_range": (.61, .82),
         "measurements": {"diameter_mm": round(rng.uniform(4, 14), 1)}},
        {"type": "effusion",    "description": "Small left pleural effusion",
         "location": "left",      "severity": "mild",     "conf_range": (.74, .93),
         "measurements": {"height_cm": round(rng.uniform(1.0, 3.5), 1)}},
        {"type": "cardiomegaly","description": "Cardiomegaly — cardiothoracic ratio elevated",
         "location": "cardiac",   "severity": "mild",     "conf_range": (.70, .89),
         "measurements": {"ct_ratio": round(rng.uniform(0.52, 0.60), 2)}},
        {"type": "atelectasis", "description": "Bibasilar atelectasis",
         "location": "bilateral", "severity": "mild",     "conf_range": (.75, .94),
         "measurements": {}},
        {"type": "pneumothorax","description": "Small right pneumothorax",
         "location": "right",     "severity": "high",     "conf_range": (.65, .87),
         "measurements": {"depth_mm": round(rng.uniform(5, 20), 1)}},
        {"type": "interstitial","description": "Increased interstitial markings",
         "location": "bilateral", "severity": "mild",     "conf_range": (.66, .85),
         "measurements": {}},
    ]

    non_chest_pool = [
        {"type": "normal",     "description": "No acute osseous abnormality",
         "location": "global", "severity": "none", "conf_range": (.90, .98),
         "measurements": {}},
        {"type": "fracture",   "description": "Cortical irregularity — possible non-displaced fracture",
         "location": "lateral","severity": "moderate", "conf_range": (.62, .84),
         "measurements": {}},
        {"type": "soft_tissue","description": "Soft tissue swelling",
         "location": "local",  "severity": "mild",     "conf_range": (.78, .95),
         "measurements": {}},
    ]

    pool = chest_pool if is_chest else non_chest_pool

    # ── pick findings deterministically ───────────────────────────────────
    # 70 % chance of normal-only, 30 % chance of 1-3 pathological findings
    normal_only = rng.random() < 0.70

    if normal_only:
        chosen = [pool[0]]   # normal finding
    else:
        # always include normal finding, then pick 1-2 pathological
        n_path = rng.randint(1, 2)
        chosen = [pool[0]] + rng.sample(pool[1:], min(n_path, len(pool) - 1))

    # ── assign confidence scores ───────────────────────────────────────────
    findings = []
    for idx, f in enumerate(chosen):
        lo, hi = f["conf_range"]
        findings.append({
            "id"          : f"f{idx + 1}",
            "type"        : f["type"],
            "description" : f["description"],
            "location"    : f["location"],
            "confidence"  : round(rng.uniform(lo, hi), 2),
            "severity"    : f["severity"],
            "measurements": f["measurements"],
        })

    # ── impression text ────────────────────────────────────────────────────
    normal = all(f["severity"] == "none" for f in findings)
    critical = any(f["severity"] == "high" for f in findings)
    follow_up = not normal

    if normal:
        impression = ("No acute cardiopulmonary process identified. "
                      "Lungs are clear bilaterally. "
                      "Cardiac silhouette is within normal limits.")
    elif critical:
        path_desc = "; ".join(f["description"] for f in findings
                              if f["severity"] != "none")
        impression = (f"CRITICAL FINDING: {path_desc}. "
                      "Immediate clinical correlation and intervention recommended.")
    else:
        path_desc = "; ".join(f["description"] for f in findings
                              if f["severity"] != "none")
        impression = (f"Findings noted: {path_desc}. "
                      "Clinical correlation recommended. "
                      "Follow-up imaging may be warranted.")

    return findings, impression, normal, follow_up
