"""US analyzer — abdomen / pelvis / thyroid."""

import hashlib, random
from typing import Tuple


def _seeded(iid: str) -> random.Random:
    return random.Random(int(hashlib.md5(("us"+iid).encode()).hexdigest()[:8], 16))


_ABDOMEN_POOL = [
    {"type":"normal",  "description":"Normal hepatobiliary and renal US",
     "location":"global","severity":"none","conf_range":(.91,.99),"measurements":{}},
    {"type":"cyst",    "description":"Simple hepatic cyst",
     "location":"liver","severity":"low","conf_range":(.88,.97),
     "measurements":{"diameter_mm":None}},
    {"type":"gallstone","description":"Cholelithiasis — multiple gallstones",
     "location":"gallbladder","severity":"mild","conf_range":(.85,.98),"measurements":{}},
    {"type":"steatosis","description":"Increased hepatic echogenicity — steatosis",
     "location":"liver","severity":"mild","conf_range":(.78,.93),"measurements":{}},
    {"type":"hydronephrosis","description":"Mild right hydronephrosis",
     "location":"right kidney","severity":"moderate","conf_range":(.75,.91),"measurements":{}},
    {"type":"ascites", "description":"Small volume free fluid in abdomen",
     "location":"peritoneum","severity":"moderate","conf_range":(.80,.95),"measurements":{}},
]
_THYROID_POOL = [
    {"type":"normal",  "description":"Normal thyroid appearance",
     "location":"global","severity":"none","conf_range":(.90,.98),"measurements":{}},
    {"type":"nodule",  "description":"Hypoechoic thyroid nodule — TI-RADS 3",
     "location":"right lobe","severity":"low","conf_range":(.72,.90),
     "measurements":{"diameter_mm":None}},
    {"type":"goitre",  "description":"Multinodular goitre",
     "location":"bilateral","severity":"mild","conf_range":(.82,.96),"measurements":{}},
]


def run(ds, instance_id: str) -> Tuple[list, str, bool, bool]:
    rng       = _seeded(instance_id)
    body_part = str(ds.get("BodyPartExamined","ABDOMEN")).upper()
    pool      = _THYROID_POOL if "THYROID" in body_part or "NECK" in body_part else _ABDOMEN_POOL

    normal_only = rng.random() < 0.60
    chosen = [pool[0]] if normal_only else         [pool[0]] + rng.sample(pool[1:], min(rng.randint(1,2), len(pool)-1))

    findings = []
    for idx, f in enumerate(chosen):
        lo, hi = f["conf_range"]
        m = {}
        for k, v in f["measurements"].items():
            if v is None:
                m[k] = round(rng.uniform(5, 35), 1)
        findings.append({
            "id":f"f{idx+1}", "type":f["type"],
            "description":f["description"], "location":f["location"],
            "confidence":round(rng.uniform(lo,hi),2),
            "severity":f["severity"], "measurements":m,
        })

    normal    = all(f["severity"]=="none" for f in findings)
    follow_up = not normal
    if normal:
        impression = "Normal ultrasound examination. No focal abnormality detected."
    else:
        desc = "; ".join(f["description"] for f in findings if f["severity"]!="none")
        impression = f"US findings: {desc}. Clinical correlation recommended."

    return findings, impression, normal, follow_up
