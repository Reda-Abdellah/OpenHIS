#!/usr/bin/env python3
"""
Generates a synthetic CR DICOM file accepted by Orthanc.
Usage: python3 generate_sample.py [output_path]
Default output: scripts/sample.dcm
"""

import sys
import datetime
import numpy as np

try:
    import pydicom
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.uid import generate_uid, ExplicitVRLittleEndian
except ImportError:
    print("[!] pydicom not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pydicom", "numpy", "-q"])
    import pydicom
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.uid import generate_uid, ExplicitVRLittleEndian


def build_cr_dicom(output_path: str) -> None:
    sop_instance_uid = generate_uid()
    study_uid        = generate_uid()
    series_uid       = generate_uid()

    # ── File Meta Information (Part 10 header) ────────────────────────────────
    file_meta = FileMetaDataset()
    file_meta.FileMetaInformationVersion    = b"\x00\x01"
    file_meta.MediaStorageSOPClassUID       = "1.2.840.10008.5.1.4.1.1.1"  # CR Image Storage
    file_meta.MediaStorageSOPInstanceUID    = sop_instance_uid
    file_meta.TransferSyntaxUID             = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID        = generate_uid()
    file_meta.ImplementationVersionName     = "PACS-DEMO-1.0"

    ds = FileDataset(output_path, {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.is_implicit_VR   = False
    ds.is_little_endian = True

    now      = datetime.datetime.now()
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S.%f")

    # ── Patient ───────────────────────────────────────────────────────────────
    ds.PatientName      = "DEMO^PATIENT^CR"
    ds.PatientID        = "DEMO-CR-001"
    ds.PatientBirthDate = "19800101"
    ds.PatientSex       = "M"

    # ── Study ─────────────────────────────────────────────────────────────────
    ds.StudyInstanceUID         = study_uid
    ds.StudyDate                = date_str
    ds.StudyTime                = time_str
    ds.ReferringPhysicianName   = ""
    ds.StudyID                  = "1"
    ds.AccessionNumber          = ""
    ds.StudyDescription         = "PACS Demo Synthetic CR"

    # ── Series ────────────────────────────────────────────────────────────────
    ds.SeriesInstanceUID  = series_uid
    ds.Modality           = "CR"
    ds.SeriesNumber       = 1
    ds.SeriesDate         = date_str
    ds.SeriesTime         = time_str
    ds.SeriesDescription  = "Synthetic Chest PA"
    ds.BodyPartExamined   = "CHEST"
    ds.PatientPosition    = "PA"
    ds.ViewPosition       = "PA"

    # ── SOP / Instance ────────────────────────────────────────────────────────
    ds.SOPClassUID    = "1.2.840.10008.5.1.4.1.1.1"
    ds.SOPInstanceUID = sop_instance_uid
    ds.InstanceNumber = 1
    ds.ContentDate    = date_str
    ds.ContentTime    = time_str

    # ── Image geometry ────────────────────────────────────────────────────────
    ROWS, COLS             = 512, 512
    ds.SamplesPerPixel         = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows                    = ROWS
    ds.Columns                 = COLS
    ds.BitsAllocated           = 16
    ds.BitsStored              = 12
    ds.HighBit                 = 11
    ds.PixelRepresentation     = 0
    ds.WindowCenter            = 2048
    ds.WindowWidth             = 4096
    ds.RescaleIntercept        = 0
    ds.RescaleSlope            = 1

    # ── Synthetic pixel data (plausible chest X-ray pattern) ──────────────────
    rng    = np.random.default_rng(42)
    pixels = rng.integers(600, 900, (ROWS, COLS), dtype=np.uint16)  # soft tissue background

    # Lung fields (darker ovals left and right)
    Y, X = np.ogrid[0:ROWS, 0:COLS]
    for cx in (170, 342):
        lung = ((Y - 270)**2 / 28000 + (X - cx)**2 / 12000) < 1
        pixels[lung] = rng.integers(200, 450, int(lung.sum()), dtype=np.uint16)

    # Ribs (bright horizontal arcs)
    for y in range(80, 430, 35):
        rib = (np.abs(Y - y) < 5) & (X > 60) & (X < COLS - 60)
        pixels[rib] = rng.integers(2600, 3200, int(rib.sum()), dtype=np.uint16)

    # Spine (central bright column)
    spine = (X > 236) & (X < 276) & (Y > 40) & (Y < 470)
    pixels[spine] = rng.integers(3000, 3800, int(spine.sum()), dtype=np.uint16)

    # Diaphragm (bright arc at bottom)
    for cx in (170, 342):
        diaphragm = (np.abs((Y - 420)**2 / 1200 + (X - cx)**2 / 8000 - 1) < 0.08)
        pixels[diaphragm] = rng.integers(2800, 3400, int(diaphragm.sum()), dtype=np.uint16)

    # Heart shadow (dark rounded mass, centre-left)
    heart = ((Y - 310)**2 / 7500 + (X - 230)**2 / 4500) < 1
    pixels[heart] = (pixels[heart] * 0.45).astype(np.uint16)

    # Clip to valid 12-bit range
    pixels = np.clip(pixels, 0, 4095).astype(np.uint16)

    ds.PixelData = pixels.tobytes()

    pydicom.dcmwrite(output_path, ds)
    print(f"[+] Synthetic CR DICOM written to: {output_path}")
    print(f"    Size: {os.path.getsize(output_path):,} bytes | Shape: {ROWS}x{COLS} | Modality: CR")


if __name__ == "__main__":
    import os
    output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sample.dcm"
    )
    build_cr_dicom(output)
