#!/usr/bin/env python3
"""
POC Chest X-Ray Pipeline  –  skeleton demonstrating the pipeline contract.

INPUT  : /data/jobs/{JOB_ID}/input/  → *.dcm  +  input.json
OUTPUT : /data/jobs/{JOB_ID}/output/ → result.json  +  overlay_001.dcm (Secondary Capture)
"""
import datetime, hashlib, json, os, random, sys
from pathlib import Path

import numpy as np
import pydicom
import pydicom.uid
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset

JOB_ID       = os.environ["JOB_ID"]
BASE         = Path(os.environ.get("JOBS_DATA_DIR", "/data/jobs")) / JOB_ID
INPUT_DIR    = BASE / "input"
OUTPUT_DIR   = BASE / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

meta  = json.loads((INPUT_DIR / "input.json").read_text())
rng   = random.Random(int(hashlib.md5(JOB_ID.encode()).hexdigest()[:8], 16))

POOL = [
    {"type": "normal",       "description": "No acute cardiopulmonary process",     "location": "bilateral", "severity": "none"},
    {"type": "opacity",      "description": "Right lower lobe opacity",              "location": "RLL",       "severity": "moderate"},
    {"type": "nodule",       "description": "Pulmonary nodule right upper lobe",     "location": "RUL",       "severity": "low"},
    {"type": "effusion",     "description": "Small left pleural effusion",           "location": "left",      "severity": "mild"},
    {"type": "cardiomegaly", "description": "Cardiomegaly",                          "location": "cardiac",   "severity": "mild"},
    {"type": "atelectasis",  "description": "Bibasilar atelectasis",                 "location": "bilateral", "severity": "mild"},
    {"type": "pneumothorax", "description": "Small right pneumothorax",              "location": "right",     "severity": "high"},
]

if rng.random() > 0.35:
    chosen = [POOL[0]]
else:
    chosen = [POOL[0]] + rng.sample(POOL[1:], rng.randint(1, 3))

findings = []
for i, f in enumerate(chosen):
    meas = {}
    if f["type"] == "opacity":
        meas = {"area_cm2": round(rng.uniform(1.5, 6.0), 1)}
    elif f["type"] == "nodule":
        meas = {"diameter_mm": round(rng.uniform(4, 14), 1)}
    elif f["type"] == "effusion":
        meas = {"height_cm": round(rng.uniform(1.0, 3.5), 1)}
    findings.append({
        "id": i + 1, "type": f["type"], "description": f["description"],
        "location": f["location"], "severity": f["severity"],
        "confidence": round(rng.uniform(0.65, 0.97), 2),
        "measurements": meas,
    })

normal    = all(f["severity"] == "none" for f in findings)
critical  = any(f["severity"] == "high" for f in findings)
follow_up = not normal

if normal:
    impression = "No acute cardiopulmonary process identified. Lungs clear bilaterally."
elif critical:
    desc = "; ".join(f["description"] for f in findings if f["severity"] == "high")
    impression = f"CRITICAL: {desc}. Immediate clinical review required."
else:
    desc = "; ".join(f["description"] for f in findings if f["severity"] != "none")
    impression = f"Findings: {desc}. Clinical correlation recommended."

# ── build Secondary Capture DICOM ────────────────────────────────────────────
dcm_files = sorted(INPUT_DIR.glob("*.dcm"))
src = None
if dcm_files:
    try: src = pydicom.dcmread(str(dcm_files[0]))
    except Exception: pass

file_meta = FileMetaDataset()
file_meta.MediaStorageSOPClassUID  = pydicom.uid.SecondaryCaptureImageStorage
file_meta.MediaStorageSOPInstanceUID = pydicom.uid.generate_uid()
file_meta.TransferSyntaxUID        = pydicom.uid.ExplicitVRLittleEndian

ds = FileDataset(None, {}, file_meta=file_meta, preamble=b"\x00"*128)
ds.is_implicit_VR = False; ds.is_little_endian = True
ds.file_meta.is_implicit_VR = False; ds.file_meta.is_little_endian = True
ds.SOPClassUID    = pydicom.uid.SecondaryCaptureImageStorage
ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
ds.Modality       = "OT"; ds.ConversionType = "WSD"
ds.SeriesInstanceUID = pydicom.uid.generate_uid()
ds.ContentDate       = datetime.datetime.now().strftime("%Y%m%d")
ds.ContentTime       = datetime.datetime.now().strftime("%H%M%S")
ds.SeriesDescription = "POC X-Ray AI Results"
ds.ImageComments     = impression[:10900]

inherit = ["PatientName","PatientID","PatientBirthDate","PatientSex",
           "StudyInstanceUID","StudyDate","StudyTime","AccessionNumber"]
if src:
    for t in inherit:
        if hasattr(src, t): setattr(ds, t, getattr(src, t))
    try:
        raw = src.pixel_array
        arr = (raw / raw.max() * 255).astype(np.uint8) if raw.max() > 0 else raw.astype(np.uint8)
        if arr.ndim == 2: arr = np.stack([arr]*3, axis=-1)
    except Exception:
        arr = np.zeros((256, 256, 3), dtype=np.uint8); arr[:,:,0] = 40
else:
    ds.PatientName = meta.get("patient_name","UNKNOWN"); ds.PatientID = meta.get("patient_id","")
    ds.StudyInstanceUID = meta.get("study_uid", pydicom.uid.generate_uid())
    arr = np.zeros((256, 256, 3), dtype=np.uint8); arr[:,:,0] = 40

ds.SamplesPerPixel = 3; ds.PhotometricInterpretation = "RGB"
ds.Rows, ds.Columns = arr.shape[0], arr.shape[1]
ds.BitsAllocated = ds.BitsStored = 8; ds.HighBit = 7
ds.PixelRepresentation = 0; ds.PlanarConfiguration = 0
ds.PixelData = arr.tobytes()
pydicom.dcmwrite(str(OUTPUT_DIR / "overlay_001.dcm"), ds)

# ── write result.json ────────────────────────────────────────────────────────
result = {
    "pipeline_id": "poc-xray", "pipeline_version": "1.0.0",
    "job_id": JOB_ID, "modality": meta.get("modality","CR"),
    "body_part": meta.get("body_part","CHEST"),
    "patient_name": meta.get("patient_name",""),
    "normal": normal, "critical": critical,
    "findings": findings, "impression": impression,
    "follow_up_recommended": follow_up,
    "output_files": ["overlay_001.dcm"],
    "analysis_notes": "POC skeleton – findings are randomly generated for demonstration only.",
}
(OUTPUT_DIR / "result.json").write_text(json.dumps(result, indent=2))
print(f"[poc-xray] {JOB_ID}: normal={normal}, findings={len(findings)}")
sys.exit(0)
