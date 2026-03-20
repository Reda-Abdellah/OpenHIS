"""MR analyzer — brain / spine / MSK."""

import hashlib, random
from typing import Tuple


def _seeded(iid: str) -> random.Random:
    return random.Random(int(hashlib.md5(("mr"+iid).encode()).hexdigest()[:8], 16))


_BRAIN_POOL = [
    {"type":"normal",   "description":"No acute intracranial abnormality",
     "location":"global","severity":"none","conf_range":(.92,.99),"measurements":{}},
    {"type":"lesion",   "description":"T2-hyperintense white matter lesion",
     "location":"periventricular","severity":"moderate","conf_range":(.68,.87),
     "measurements":{"diameter_mm":None}},
    {"type":"diffusion","description":"Restricted diffusion — possible acute infarct",
     "location":"left MCA territory","severity":"high","conf_range":(.72,.90),
     "measurements":{"volume_mL":None}},
    {"type":"enhancement","description":"Enhancing lesion post-contrast",
     "location":"right frontal","severity":"high","conf_range":(.65,.84),
     "measurements":{"diameter_mm":None}},
    {"type":"atrophy",  "description":"Mild generalised cerebral atrophy",
     "location":"global","severity":"mild","conf_range":(.80,.96),"measurements":{}},
]
_SPINE_POOL = [
    {"type":"normal",   "description":"No focal disc herniation or cord compression",
     "location":"global","severity":"none","conf_range":(.90,.98),"measurements":{}},
    {"type":"disc",     "description":"L4/L5 disc herniation with nerve root compression",
     "location":"L4/L5","severity":"moderate","conf_range":(.74,.92),
     "measurements":{"protrusion_mm":None}},
    {"type":"stenosis", "description":"Central canal stenosis",
     "location":"L3/L4","severity":"moderate","conf_range":(.70,.88),
     "measurements":{}},
]
_KNEE_POOL = [
    {"type":"normal",   "description":"No internal derangement of the knee",
     "location":"global","severity":"none","conf_range":(.91,.98),"measurements":{}},
    {"type":"meniscus", "description":"Medial meniscus posterior horn tear",
     "location":"medial","severity":"moderate","conf_range":(.75,.92),"measurements":{}},
    {"type":"acl",      "description":"ACL disruption — complete tear",
     "location":"intercondylar","severity":"high","conf_range":(.80,.95),"measurements":{}},
    {"type":"cartilage","description":"Articular cartilage thinning — medial compartment",
     "location":"medial","severity":"mild","conf_range":(.72,.88),"measurements":{}},
]

_MEAS_DEFAULTS = {"diameter_mm":(4,20),"volume_mL":(0.5,8.0),"protrusion_mm":(3,10)}


def run(ds, instance_id: str) -> Tuple[list, str, bool, bool]:
    rng       = _seeded(instance_id)
    body_part = str(ds.get("BodyPartExamined","BRAIN")).upper()
    seq       = str(ds.get("SequenceName","SE")).upper()

    pool = (_KNEE_POOL  if "KNEE" in body_part or "MSK" in body_part else
            _SPINE_POOL if "SPINE" in body_part or "LUMBAR" in body_part else
            _BRAIN_POOL)

    normal_only = rng.random() < 0.65
    chosen = [pool[0]] if normal_only else         [pool[0]] + rng.sample(pool[1:], min(rng.randint(1,2), len(pool)-1))

    findings = []
    for idx, f in enumerate(chosen):
        lo, hi = f["conf_range"]
        m = {}
        for k, v in f["measurements"].items():
            if v is None:
                lo_m, hi_m = _MEAS_DEFAULTS.get(k,(1,10))
                m[k] = round(rng.uniform(lo_m, hi_m), 1)
        findings.append({
            "id":f"f{idx+1}", "type":f["type"],
            "description":f["description"], "location":f["location"],
            "confidence":round(rng.uniform(lo,hi),2),
            "severity":f["severity"], "measurements":m,
        })

    normal    = all(f["severity"]=="none" for f in findings)
    follow_up = not normal
    critical  = any(f["severity"]=="high" for f in findings)

    if normal:
        impression = f"Normal MR {body_part.title()}. No acute abnormality identified."
    elif critical:
        desc = "; ".join(f["description"] for f in findings if f["severity"]=="high")
        impression = f"CRITICAL finding on MR: {desc}. Urgent neurology review required."
    else:
        desc = "; ".join(f["description"] for f in findings if f["severity"]!="none")
        impression = f"MR findings: {desc}. Recommend clinical correlation."

    return findings, impression, normal, follow_up
