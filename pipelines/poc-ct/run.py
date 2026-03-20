#!/usr/bin/env python3
"""
POC CT Analyzer Pipeline  –  skeleton demonstrating the pipeline contract.

INPUT  : /data/jobs/{JOB_ID}/input/  → *.dcm  +  input.json
OUTPUT : /data/jobs/{JOB_ID}/output/ → result.json  +  seg_mask_001.dcm (Secondary Capture)
"""
import datetime, hashlib, json, os, random, sys
from pathlib import Path

import numpy as np
import pydicom
import pydicom.uid
from pydicom.dataset import FileDataset, FileMetaDataset

JOB_ID     = os.environ["JOB_ID"]
BASE       = Path(os.environ.get("JOBS_DATA_DIR", "/data/jobs")) / JOB_ID
INPUT_DIR  = BASE / "input"
OUTPUT_DIR = BASE / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

meta = json.loads((INPUT_DIR / "input.json").read_text())
rng  = random.Random(int(hashlib.md5(("ct" + JOB_ID).encode()).hexdigest()[:8], 16))

CHEST_POOL = [
    {"type": "normal",      "description": "No acute intrathoracic process",           "location": "bilateral", "severity": "none"},
    {"type": "nodule",      "description": "Pulmonary nodule right upper lobe",         "location": "RUL",       "severity": "low",      "meas": {"diameter_mm": (4, 20)}},
    {"type": "groundglass", "description": "Ground-glass opacity left lower lobe",      "location": "LLL",       "severity": "moderate",  "meas": {"area_cm2": (0.8, 5.0)}},
    {"type": "effusion",    "description": "Small bilateral pleural effusions",         "location": "bilateral", "severity": "mild"},
    {"type": "pe",          "description": "Filling defect – possible pulmonary emboli","location": "right PA",  "severity": "high"},
    {"type": "emphysema",   "description": "Centrilobular emphysema upper lobes",       "location": "bilateral", "severity": "mild"},
]
HEAD_POOL = [
    {"type": "normal",       "description": "No acute intracranial abnormality",         "location": "global",     "severity": "none"},
    {"type": "hyperdensity", "description": "Hyperdense focus – possible haemorrhage",   "location": "right BG",   "severity": "high",    "meas": {"volume_mL": (0.5, 8.0)}},
    {"type": "hypodensity",  "description": "Hypodense region – possible ischaemia",     "location": "left MCA",   "severity": "high",    "meas": {"area_cm2": (1.0, 6.0)}},
    {"type": "atrophy",      "description": "Mild cortical atrophy",                     "location": "global",     "severity": "mild"},
]
ABD_POOL = [
    {"type": "normal",  "description": "No acute abdominal abnormality",             "location": "global",       "severity": "none"},
    {"type": "lesion",  "description": "Liver hypodense lesion – possible cyst",     "location": "liver",        "severity": "low",      "meas": {"diameter_mm": (5, 30)}},
    {"type": "stone",   "description": "Right renal calculus",                       "location": "right kidney", "severity": "moderate", "meas": {"diameter_mm": (3, 12)}},
    {"type": "appendix","description": "Dilated appendix – possible appendicitis",   "location": "RIF",          "severity": "high",     "meas": {"diameter_mm": (7, 15)}},
]

body_part = meta.get("body_part", "CHEST").upper()
pool = HEAD_POOL if "HEAD" in body_part or "BRAIN" in body_part \
    else ABD_POOL if "ABD" in body_part or "PELV" in body_part \
    else CHEST_POOL

normal_only = rng.random() > 0.40
chosen = [pool[0]] if normal_only else [pool[0]] + rng.sample(pool[1:], min(rng.randint(1,2), len(pool)-1))

findings = []
for i, f in enumerate(chosen):
    meas = {}
    for k, (lo, hi) in f.get("meas", {}).items():
        meas[k] = round(rng.uniform(lo, hi), 1)
    findings.append({
        "id": i+1, "type": f["type"], "description": f["description"],
        "location": f["location"], "severity": f["severity"],
        "confidence": round(rng.uniform(0.65, 0.97), 2),
        "measurements": meas,
    })

normal   = all(f["severity"] == "none" for f in findings)
critical = any(f["severity"] == "high"  for f in findings)
follow_up = not normal

if normal:
    impression = f"No acute findings on CT {body_part.title()}. Normal study."
elif critical:
    desc = "; ".join(f["description"] for f in findings if f["severity"] == "high")
    impression = f"CRITICAL: {desc}. Urgent clinical review required."
else:
    desc = "; ".join(f["description"] for f in findings if f["severity"] != "none")
    impression = f"Findings: {desc}. Clinical correlation recommended."

# ── build colour overlay Secondary Capture (simulates segmentation mask) ─────
dcm_files = sorted(INPUT_DIR.glob("*.dcm"))
src = None
if dcm_files:
    try: src = pydicom.dcmread(str(dcm_files[0]))
    except Exception: pass

h, w = 256, 256
base_arr = np.zeros((h, w, 3), dtype=np.uint8)
if src:
    try:
        raw = src.pixel_array
        gray = (raw / raw.max() * 200).astype(np.uint8) if raw.max() > 0 else raw.astype(np.uint8)
        if gray.ndim > 2: gray = gray[:,:,0]
        gray = gray[:h, :w] if gray.shape[0] >= h and gray.shape[1] >= w else np.pad(gray,((0,max(0,h-gray.shape[0])),(0,max(0,w-gray.shape[1]))))[:h,:w]
        base_arr[:,:,:] = gray[:,:,np.newaxis]
    except Exception: pass

# draw random "mask" blobs for each non-normal finding
for idx, f in enumerate(findings):
    if f["severity"] == "none": continue
    color = ([255,80,80] if f["severity"]=="high" else [255,180,50] if f["severity"]=="moderate" else [100,200,100])
    cy, cx = rng.randint(40,h-40), rng.randint(40,w-40)
    r = rng.randint(8, 22)
    Y, X = np.ogrid[:h, :w]
    mask = (X-cx)**2 + (Y-cy)**2 <= r**2
    base_arr[mask] = color

file_meta = FileMetaDataset()
file_meta.MediaStorageSOPClassUID   = pydicom.uid.SecondaryCaptureImageStorage
file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
file_meta.TransferSyntaxUID          = pydicom.uid.ExplicitVRLittleEndian

ds = FileDataset(None, {}, file_meta=file_meta, preamble=b"\x00"*128)
ds.is_implicit_VR = False; ds.is_little_endian = True
ds.file_meta.is_implicit_VR = False; ds.file_meta.is_little_endian = True
ds.SOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
ds.Modality = "OT"; ds.ConversionType = "WSD"
ds.SeriesInstanceUID = pydicom.uid.generate_uid()
ds.ContentDate = datetime.datetime.now().strftime("%Y%m%d")
ds.ContentTime = datetime.datetime.now().strftime("%H%M%S")
ds.SeriesDescription = "POC CT AI Mask"
ds.ImageComments = impression[:10900]

for t in ["PatientName","PatientID","PatientBirthDate","PatientSex",
          "StudyInstanceUID","StudyDate","StudyTime","AccessionNumber"]:
    if src and hasattr(src, t): setattr(ds, t, getattr(src, t))
    elif t == "PatientName": ds.PatientName = meta.get("patient_name","UNKNOWN")
    elif t == "PatientID":   ds.PatientID   = meta.get("patient_id","")
    elif t == "StudyInstanceUID": ds.StudyInstanceUID = meta.get("study_uid", pydicom.uid.generate_uid())

ds.SamplesPerPixel = 3; ds.PhotometricInterpretation = "RGB"
ds.Rows, ds.Columns = h, w
ds.BitsAllocated = ds.BitsStored = 8; ds.HighBit = 7
ds.PixelRepresentation = 0; ds.PlanarConfiguration = 0
ds.PixelData = base_arr.tobytes()
pydicom.dcmwrite(str(OUTPUT_DIR / "seg_mask_001.dcm"), ds)

result = {
    "pipeline_id": "poc-ct", "pipeline_version": "1.0.0",
    "job_id": JOB_ID, "modality": meta.get("modality","CT"),
    "body_part": body_part, "patient_name": meta.get("patient_name",""),
    "normal": normal, "critical": critical,
    "findings": findings, "impression": impression,
    "follow_up_recommended": follow_up,
    "output_files": ["seg_mask_001.dcm"],
    "analysis_notes": "POC skeleton – findings and mask blobs are randomly generated for demonstration only.",
}
(OUTPUT_DIR / "result.json").write_text(json.dumps(result, indent=2))
print(f"[poc-ct] {JOB_ID}: normal={normal}, findings={len(findings)}, body_part={body_part}")
sys.exit(0)
