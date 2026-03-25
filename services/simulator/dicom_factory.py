"""
DICOM Factory — Step 2.3-A
Supported modalities: CR, DX, CT, MR, US
All builders return list[bytes] (one element per DICOM instance / slice).
"""

import io
import datetime
import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian

from presets import MODALITY_PRESETS


# ── helpers ───────────────────────────────────────────────────────────────────

def _now():
    n = datetime.datetime.now()
    return n.strftime("%Y%m%d"), n.strftime("%H%M%S.%f")


def _file_meta(sop_class: str, sop_instance: str) -> FileMetaDataset:
    m = FileMetaDataset()
    m.FileMetaInformationVersion  = b"\x00\x01"
    m.MediaStorageSOPClassUID     = sop_class
    m.MediaStorageSOPInstanceUID  = sop_instance
    m.TransferSyntaxUID           = ExplicitVRLittleEndian
    m.ImplementationClassUID      = generate_uid()
    m.ImplementationVersionName   = "PACS-SIM-2.3"
    return m


def _base(sop_class, patient, study_uid, series_uid, sop_uid,
          modality, date_str, time_str) -> FileDataset:
    ds = FileDataset("", {}, file_meta=_file_meta(sop_class, sop_uid),
                     preamble=b"\x00" * 128)
    ds.is_implicit_VR   = False
    ds.is_little_endian = True

    ds.PatientName      = patient.get("patient_name", "SIMULATOR^DEMO")
    ds.PatientID        = patient.get("patient_id",   "SIM-001")
    ds.PatientBirthDate = patient.get("dob", "").replace("-", "")
    ds.PatientSex       = patient.get("sex", "O")

    ds.StudyInstanceUID       = study_uid
    ds.StudyDate              = date_str
    ds.StudyTime              = time_str
    ds.StudyID                = "1"
    ds.AccessionNumber        = patient.get("accession",  "")
    ds.StudyDescription       = patient.get("study_desc",  "Simulated Exam")
    ds.ReferringPhysicianName = "SIMULATOR"

    ds.SeriesInstanceUID  = series_uid
    ds.SeriesNumber       = 1
    ds.SeriesDate         = date_str
    ds.SeriesTime         = time_str
    ds.Modality           = modality

    ds.SOPClassUID  = sop_class
    ds.SOPInstanceUID = sop_uid
    ds.ContentDate  = date_str
    ds.ContentTime  = time_str

    return ds


def _image_tags(ds, rows, cols, bits=12, signed=False):
    ds.SamplesPerPixel           = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows                      = rows
    ds.Columns                   = cols
    ds.BitsAllocated             = 16
    ds.BitsStored                = bits
    ds.HighBit                   = bits - 1
    ds.PixelRepresentation       = 1 if signed else 0


def _to_bytes(ds) -> bytes:
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds)
    return buf.getvalue()


# ── pixel generators ──────────────────────────────────────────────────────────

def _xray_pixels(rows, cols, body_part):
    rng  = np.random.default_rng(42)
    img  = rng.integers(600, 900, (rows, cols), dtype=np.uint16)
    Y, X = np.mgrid[0:rows, 0:cols]
    cx, cy = cols // 2, int(rows * 0.52)
    body = body_part.upper()

    if body == "CHEST":
        for lx in (int(cols*.33), int(cols*.67)):
            m = ((Y-cy)**2/(rows*.26)**2 + (X-lx)**2/(cols*.19)**2) < 1
            img[m] = rng.integers(180, 420, int(m.sum()), dtype=np.uint16)
        step = max(1, rows // 14)
        for y in range(int(rows*.12), int(rows*.82), step):
            m = (np.abs(Y-y) < max(2, rows//120)) & (X>int(cols*.08)) & (X<int(cols*.92))
            img[m] = rng.integers(2400, 3100, int(m.sum()), dtype=np.uint16)
        spw = max(4, cols//22)
        m = (X>cx-spw) & (X<cx+spw) & (Y>int(rows*.05)) & (Y<int(rows*.88))
        img[m] = rng.integers(2800, 3600, int(m.sum()), dtype=np.uint16)
        m = ((Y-int(rows*.58))**2/(rows*.21)**2 + (X-int(cols*.44))**2/(cols*.16)**2) < 1
        img[m] = (img[m]*.42).astype(np.uint16)
    elif body in ("HAND","WRIST","ELBOW"):
        img[:] = rng.integers(300, 500, (rows, cols), dtype=np.uint16)
        for i in range(5):
            bx, bw = int(cols*(0.15+i*.16)), max(4, cols//25)
            m = (X>bx-bw) & (X<bx+bw) & (Y>int(rows*.1)) & (Y<int(rows*.85))
            img[m] = rng.integers(3000, 3800, int(m.sum()), dtype=np.uint16)
    elif body in ("KNEE","HIP","ANKLE","FOOT"):
        img[:] = rng.integers(400, 700, (rows, cols), dtype=np.uint16)
        for bx in (int(cols*.38), int(cols*.62)):
            bw = max(6, cols//18)
            m = (X>bx-bw) & (X<bx+bw) & (Y>rows//4) & (Y<rows*3//4)
            img[m] = rng.integers(3200, 4000, int(m.sum()), dtype=np.uint16)
        r0, r1 = int(rows*.45), int(rows*.55)
        img[r0:r1, :] = (img[r0:r1, :] * .5).astype(np.uint16)
    elif body == "SPINE":
        img[:] = rng.integers(500, 800, (rows, cols), dtype=np.uint16)
        spw = max(8, cols//8)
        m = (X>cx-spw) & (X<cx+spw)
        img[m] = rng.integers(2600, 3400, int(m.sum()), dtype=np.uint16)
    elif body in ("SKULL","HEAD"):
        img[:] = rng.integers(200, 400, (rows, cols), dtype=np.uint16)
        m = ((Y-cy)**2/(rows*.42)**2 + (X-cx)**2/(cols*.38)**2) < 1
        img[m] = rng.integers(2800, 3600, int(m.sum()), dtype=np.uint16)
        m = ((Y-cy)**2/(rows*.35)**2 + (X-cx)**2/(cols*.31)**2) < 1
        img[m] = rng.integers(600, 1000, int(m.sum()), dtype=np.uint16)
    else:
        img[:] = rng.integers(400, 1200, (rows, cols), dtype=np.uint16)

    return np.clip(img, 0, 4095).astype(np.uint16)


def _ct_slice_pixels(rows, cols, body_part, z_norm):
    """z_norm: 0.0 = top of volume, 1.0 = bottom"""
    rng  = np.random.default_rng(int(z_norm * 1000))
    img  = np.full((rows, cols), -1000, dtype=np.int16)   # air background
    Y, X = np.ogrid[0:rows, 0:cols]
    cx, cy = cols // 2, rows // 2
    body = body_part.upper()

    if body in ("CHEST",):
        # Body contour (soft tissue ~50 HU)
        body_mask = ((Y-cy)**2/(rows*.44)**2 + (X-cx)**2/(cols*.42)**2) < 1
        img[body_mask] = rng.integers(20, 60, int(body_mask.sum())).astype(np.int16)
        # Lung fields
        for lx in (int(cols*.33), int(cols*.67)):
            scale = 0.9 - 0.4*z_norm
            lung = ((Y-int(rows*(0.42+.1*z_norm)))**2/(rows*scale*.32)**2 +
                    (X-lx)**2/(cols*.19)**2) < 1
            img[lung] = rng.integers(-850, -700, int(lung.sum())).astype(np.int16)
        # Spine
        spw = max(6, cols//18)
        spine = (X>cx-spw) & (X<cx+spw) & (Y>int(rows*.55)) & body_mask
        img[spine] = rng.integers(200, 500, int(spine.sum())).astype(np.int16)
        # Ribs (bright dots in axial)
        for angle in np.linspace(0.2, np.pi-0.2, 8):
            rx = int(cx + cols*.36 * np.cos(angle))
            ry = int(cy - rows*.36 * np.sin(angle))
            rw = max(3, rows//30)
            rib = (Y>ry-rw) & (Y<ry+rw) & (X>rx-rw) & (X<rx+rw)
            img[rib] = rng.integers(300, 600, int(rib.sum())).astype(np.int16)
        # Aorta (round, mid-upper)
        if z_norm < .7:
            ao = ((Y-int(rows*.40))**2/(rows*.04)**2 +
                  (X-int(cols*.52))**2/(cols*.03)**2) < 1
            img[ao] = rng.integers(30, 50, int(ao.sum())).astype(np.int16)
        # Heart (lower slices)
        if z_norm > .3:
            ht = ((Y-int(rows*.48))**2/(rows*.22)**2 +
                  (X-int(cols*.46))**2/(cols*.18)**2) < 1
            img[ht & body_mask] = rng.integers(40, 80,
                int((ht & body_mask).sum())).astype(np.int16)

    elif body == "HEAD":
        skull_o = ((Y-cy)**2/(rows*.44)**2 + (X-cx)**2/(cols*.40)**2) < 1
        skull_i = ((Y-cy)**2/(rows*.38)**2 + (X-cx)**2/(cols*.34)**2) < 1
        skull = skull_o & ~skull_i
        img[skull_o] = rng.integers(25, 45, int(skull_o.sum())).astype(np.int16)
        img[skull]   = rng.integers(600, 900, int(skull.sum())).astype(np.int16)
        # White matter
        wm = skull_i & ((Y-cy)**2/(rows*.28)**2 + (X-cx)**2/(cols*.24)**2 > 1)
        img[wm] = rng.integers(20, 35, int(wm.sum())).astype(np.int16)
        # Ventricles (CSF)
        if .3 < z_norm < .75:
            for vx in (int(cx-.08*cols), int(cx+.08*cols)):
                vent = ((Y-cy)**2/(rows*.09)**2 + (X-vx)**2/(cols*.06)**2) < 1
                img[vent & skull_i] = rng.integers(0, 15,
                    int((vent & skull_i).sum())).astype(np.int16)

    elif body == "ABDOMEN":
        body_mask = ((Y-cy)**2/(rows*.47)**2 + (X-cx)**2/(cols*.44)**2) < 1
        img[body_mask] = rng.integers(20, 55, int(body_mask.sum())).astype(np.int16)
        # Liver (right side)
        if z_norm < .6:
            liver = ((Y-int(rows*.42))**2/(rows*.28)**2 +
                     (X-int(cols*.65))**2/(cols*.22)**2) < 1
            img[liver & body_mask] = rng.integers(50, 65,
                int((liver & body_mask).sum())).astype(np.int16)
        # Spine
        spw = max(5, cols//20)
        _spine_mask = (X>cx-spw) & (X<cx+spw) & (Y>int(rows*.6)) & body_mask
        img[_spine_mask] = rng.integers(200, 450, int(_spine_mask.sum())).astype(np.int16)
        # Bowel gas (dark spots)
        for _ in range(4):
            bx = rng.integers(int(cols*.25), int(cols*.75))
            by = rng.integers(int(rows*.3), int(rows*.7))
            bw = max(3, rows//25)
            g  = (Y>by-bw) & (Y<by+bw) & (X>bx-bw) & (X<bx+bw) & body_mask
            img[g] = -950
    else:
        body_mask = ((Y-cy)**2/(rows*.44)**2 + (X-cx)**2/(cols*.44)**2) < 1
        img[body_mask] = rng.integers(20, 60, int(body_mask.sum())).astype(np.int16)

    noise = rng.integers(-8, 8, (rows, cols), dtype=np.int16)
    return np.clip(img.astype(np.int32) + noise, -1024, 3071).astype(np.int16)


def _mr_slice_pixels(rows, cols, body_part, seq_type, z_norm):
    rng  = np.random.default_rng(int(z_norm * 1000) + 1)
    img  = np.zeros((rows, cols), dtype=np.uint16)
    Y, X = np.ogrid[0:rows, 0:cols]
    cx, cy = cols // 2, rows // 2
    body = body_part.upper()
    t2   = seq_type.upper() in ("T2", "FLAIR", "EPI", "STIR")

    if body == "BRAIN":
        skull_o = ((Y-cy)**2/(rows*.44)**2 + (X-cx)**2/(cols*.40)**2) < 1
        skull_i = ((Y-cy)**2/(rows*.37)**2 + (X-cx)**2/(cols*.33)**2) < 1
        # Background = 0 (black)
        # Skull (low signal)
        img[skull_o & ~skull_i] = rng.integers(50, 120,
            int((skull_o & ~skull_i).sum()), dtype=np.uint16)
        # Brain parenchyma: GM
        gm = skull_i & ((Y-cy)**2/(rows*.28)**2 + (X-cx)**2/(cols*.26)**2 > 1)
        img[gm] = (rng.integers(1400, 1800, int(gm.sum()), dtype=np.uint16)
                   if t2 else rng.integers(1800, 2200, int(gm.sum()), dtype=np.uint16))
        # WM
        wm = ((Y-cy)**2/(rows*.26)**2 + (X-cx)**2/(cols*.24)**2) < 1
        img[wm & skull_i] = (rng.integers(1000, 1400, int((wm & skull_i).sum()),dtype=np.uint16)
                              if t2 else rng.integers(2400, 2900,
                              int((wm & skull_i).sum()), dtype=np.uint16))
        # Ventricles
        if .3 < z_norm < .75:
            for vx in (int(cx-.09*cols), int(cx+.09*cols)):
                vent = ((Y-cy)**2/(rows*.09)**2 + (X-vx)**2/(cols*.06)**2) < 1
                v    = vent & skull_i
                img[v] = (rng.integers(3500, 4000, int(v.sum()), dtype=np.uint16)
                          if t2 else rng.integers(200, 400, int(v.sum()), dtype=np.uint16))
        # Sulci (thin dark lines on surface in T1)
        if not t2:
            theta = np.linspace(0, 2*np.pi, 24, endpoint=False)
            for th in theta:
                sx = int(cx + cols*.30*np.cos(th))
                sy = int(cy + rows*.30*np.sin(th))
                if 0 < sx < cols and 0 < sy < rows:
                    rr, cc = max(0,sy-1), max(0,sx-1)
                    img[rr:rr+2, cc:cc+2] = 0

    elif body == "KNEE":
        # Sagittal-like knee
        img[:] = rng.integers(100, 300, (rows, cols), dtype=np.uint16)
        # Femoral condyle
        fc = ((Y-int(rows*.4))**2/(rows*.18)**2 + (X-cx)**2/(cols*.28)**2) < 1
        img[fc] = (rng.integers(3000, 3800, int(fc.sum()), dtype=np.uint16)
                   if not t2 else rng.integers(1200, 1800, int(fc.sum()),dtype=np.uint16))
        # Tibia
        tb = ((Y-int(rows*.62))**2/(rows*.14)**2 + (X-cx)**2/(cols*.24)**2) < 1
        img[tb] = (rng.integers(3000, 3800, int(tb.sum()), dtype=np.uint16)
                   if not t2 else rng.integers(1200, 1800, int(tb.sum()),dtype=np.uint16))
        # Cartilage
        cart = ((Y-int(rows*.52))**2/(rows*.04)**2 + (X-cx)**2/(cols*.20)**2) < 1
        img[cart] = rng.integers(2000, 2600, int(cart.sum()), dtype=np.uint16)
        # PCL/ACL (dark bands)
        for px, py in [(cx-5, int(rows*.52)), (cx+5, int(rows*.52))]:
            img[py-3:py+3, px-3:px+3] = 0
    else:
        # Generic body MR
        body_mask = ((Y-cy)**2/(rows*.44)**2 + (X-cx)**2/(cols*.44)**2) < 1
        img[body_mask] = rng.integers(1000, 2200, int(body_mask.sum()),dtype=np.uint16)
        org = ((Y-int(rows*.42))**2/(rows*.18)**2 + (X-int(cols*.62))**2/(cols*.15)**2) < 1
        img[org & body_mask] = rng.integers(2400, 3000,
            int((org & body_mask).sum()), dtype=np.uint16)

    noise = rng.integers(0, 40, (rows, cols), dtype=np.uint16)
    return np.clip(img.astype(np.int32) + noise, 0, 4095).astype(np.uint16)


def _us_pixels(rows, cols, body_part, probe_type):
    rng  = np.random.default_rng(77)
    img  = np.zeros((rows, cols), dtype=np.uint8)
    Y, X = np.ogrid[0:rows, 0:cols]

    # Fan / sector shape
    apex_y, apex_x = int(rows*.08), cols // 2
    half_angle = np.radians(50 if probe_type == "Phased" else 38)
    dist  = np.sqrt((Y - apex_y)**2 + (X - apex_x)**2).astype(np.float32)
    angle = np.arctan2(X - apex_x, Y - apex_y)
    in_fan = (np.abs(angle) < half_angle) & (dist > rows*.08) & (dist < rows*.92)

    # Speckle noise base
    speckle = rng.integers(20, 80, (rows, cols), dtype=np.uint8)
    img[in_fan] = speckle[in_fan]

    # Organ boundary (bright arc)
    arc_r = rows * .45
    arc = in_fan & (np.abs(dist - arc_r) < rows*.018)
    img[arc] = rng.integers(160, 230, int(arc.sum()), dtype=np.uint8)

    # Second boundary
    arc2 = in_fan & (np.abs(dist - rows*.68) < rows*.012)
    img[arc2] = rng.integers(140, 200, int(arc2.sum()), dtype=np.uint8)

    # Anechoic region (cyst / vessel)
    cx_u = int(apex_x + (apex_x*.3) * np.sin(0.2))
    cy_u = int(apex_y + rows*.52)
    cyst = in_fan & ((Y-cy_u)**2/(rows*.07)**2 + (X-cx_u)**2/(cols*.06)**2 < 1)
    img[cyst] = rng.integers(0, 15, int(cyst.sum()), dtype=np.uint8)

    # Rib shadow (dark vertical stripes)
    if body_part.upper() == "ABDOMEN":
        for rx in (int(cols*.3), int(cols*.7)):
            shadow = in_fan & (X > rx-8) & (X < rx+8)
            img[shadow] = (img[shadow] * .15).astype(np.uint8)

    # Depth scale markers (left edge)
    for d in range(1, 8):
        ty = int(apex_y + rows*.12*d)
        if ty < rows:
            img[ty:ty+1, 5:15] = 200

    return img


# ── CR / DX builders (unchanged logic, now return list[bytes]) ────────────────

def _build_cr(patient, params) -> list:
    d, t = _now()
    uid = generate_uid()
    ds  = _base(MODALITY_PRESETS["CR"]["sopClass"], patient,
                generate_uid(), generate_uid(), uid, "CR", d, t)
    rows = int(params.get("rows", 2048)); cols = int(params.get("cols", 2048))
    body = params.get("body_part", "CHEST")
    ds.BodyPartExamined = body;  ds.ViewPosition = params.get("view_position","PA")
    ds.PatientPosition  = params.get("view_position","PA")
    ds.KVP = float(params.get("kvp",120)); ds.ExposureTime = int(params.get("exposure_time",20))
    ds.PixelSpacing = [float(params.get("pixel_spacing",.148))]*2
    _image_tags(ds, rows, cols, bits=12)
    ds.WindowCenter = 2048; ds.WindowWidth = 4096
    ds.RescaleIntercept = 0; ds.RescaleSlope = 1
    ds.InstanceNumber = 1
    ds.PixelData = _xray_pixels(rows, cols, body).tobytes()
    return [_to_bytes(ds)]


def _build_dx(patient, params) -> list:
    d, t = _now()
    uid = generate_uid()
    ds  = _base(MODALITY_PRESETS["DX"]["sopClass"], patient,
                generate_uid(), generate_uid(), uid, "DX", d, t)
    rows = int(params.get("rows", 2480)); cols = int(params.get("cols", 2560))
    body = params.get("body_part", "CHEST")
    ds.BodyPartExamined = body;  ds.ViewPosition = params.get("view_position","PA")
    ds.PatientPosition  = params.get("view_position","PA")
    ds.KVP = float(params.get("kvp",125)); ds.ExposureTime = int(params.get("exposure_time",16))
    ds.PixelSpacing = [float(params.get("pixel_spacing",.139))]*2
    _image_tags(ds, rows, cols, bits=12)
    ds.WindowCenter = 2048; ds.WindowWidth = 4096
    ds.RescaleIntercept = 0; ds.RescaleSlope = 1
    ds.InstanceNumber = 1
    ds.PixelData = _xray_pixels(rows, cols, body).tobytes()
    return [_to_bytes(ds)]


# ── CT builder ────────────────────────────────────────────────────────────────

def _build_ct(patient, params) -> list:
    d, t       = _now()
    study_uid  = generate_uid()
    series_uid = generate_uid()
    sop_class  = MODALITY_PRESETS["CT"]["sopClass"]

    rows    = int(params.get("rows", 512))
    cols    = int(params.get("cols", 512))
    n       = int(params.get("slice_count",   64))
    thick   = float(params.get("slice_thickness", 1.25))
    fov     = float(params.get("fov",    360))
    kvp     = float(params.get("kvp",    120))
    mas     = float(params.get("mas",    200))
    body    = params.get("body_part", "CHEST")
    ps      = round(fov / rows, 4)

    instances = []
    for i in range(n):
        z_norm  = i / max(n - 1, 1)
        sop_uid = generate_uid()
        ds = _base(sop_class, patient, study_uid, series_uid, sop_uid, "CT", d, t)
        ds.BodyPartExamined = body
        ds.SliceThickness   = thick
        ds.KVP              = kvp
        ds.Exposure         = int(mas)
        ds.PixelSpacing     = [ps, ps]
        ds.ImagePositionPatient = [-(fov/2), -(fov/2), round(i * thick, 2)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceLocation    = round(i * thick, 2)
        ds.InstanceNumber   = i + 1
        ds.WindowCenter     = 40
        ds.WindowWidth      = 400
        ds.RescaleIntercept = 0
        ds.RescaleSlope     = 1
        _image_tags(ds, rows, cols, bits=16, signed=True)
        ds.PixelData = _ct_slice_pixels(rows, cols, body, z_norm).tobytes()
        instances.append(_to_bytes(ds))

    return instances


# ── MR builder ────────────────────────────────────────────────────────────────

def _build_mr(patient, params) -> list:
    d, t       = _now()
    study_uid  = generate_uid()
    series_uid = generate_uid()
    sop_class  = MODALITY_PRESETS["MR"]["sopClass"]

    rows  = int(params.get("rows", 256))
    cols  = int(params.get("cols", 256))
    n     = int(params.get("slice_count",  20))
    thick = float(params.get("slice_thickness", 5.0))
    tr    = float(params.get("tr",   500))
    te    = float(params.get("te",    15))
    fa    = float(params.get("flip_angle", 90))
    seq   = params.get("sequence_type", "SE")
    body  = params.get("body_part",     "BRAIN")

    instances = []
    for i in range(n):
        z_norm  = i / max(n - 1, 1)
        sop_uid = generate_uid()
        ds = _base(sop_class, patient, study_uid, series_uid, sop_uid, "MR", d, t)
        ds.BodyPartExamined      = body
        ds.SliceThickness        = thick
        ds.RepetitionTime        = tr
        ds.EchoTime              = te
        ds.FlipAngle             = fa
        ds.SequenceName          = seq
        ds.MRAcquisitionType     = "2D"
        ds.SliceLocation         = round(i * thick, 2)
        ds.ImagePositionPatient  = [0, 0, round(i * thick, 2)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.InstanceNumber        = i + 1
        ds.WindowCenter          = 2048
        ds.WindowWidth           = 4096
        ds.RescaleIntercept      = 0
        ds.RescaleSlope          = 1
        _image_tags(ds, rows, cols, bits=12)
        ds.PixelData = _mr_slice_pixels(rows, cols, body, seq, z_norm).tobytes()
        instances.append(_to_bytes(ds))

    return instances


# ── US builder ────────────────────────────────────────────────────────────────

def _build_us(patient, params) -> list:
    d, t = _now()
    sop_uid = generate_uid()
    sop_class = MODALITY_PRESETS["US"]["sopClass"]
    ds = _base(sop_class, patient, generate_uid(), generate_uid(), sop_uid, "US", d, t)

    rows  = int(params.get("rows", 480))
    cols  = int(params.get("cols", 640))
    body  = params.get("body_part",  "ABDOMEN")
    probe = params.get("probe_type", "Convex")
    depth = float(params.get("depth", 15))
    freq  = float(params.get("frequency", 3.5))

    ds.BodyPartExamined          = body
    ds.TransducerData            = f"{probe} {freq}MHz"
    ds.SamplesPerPixel           = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows                      = rows
    ds.Columns                   = cols
    ds.BitsAllocated             = 8
    ds.BitsStored                = 8
    ds.HighBit                   = 7
    ds.PixelRepresentation       = 0
    ds.InstanceNumber            = 1
    ds.WindowCenter              = 128
    ds.WindowWidth               = 256
    ds.RescaleIntercept          = 0
    ds.RescaleSlope              = 1
    ds.PixelData = _us_pixels(rows, cols, body, probe).tobytes()
    return [_to_bytes(ds)]


# ── dispatch ──────────────────────────────────────────────────────────────────

_BUILDERS = {"CR": _build_cr, "DX": _build_dx,
             "CT": _build_ct, "MR": _build_mr, "US": _build_us}
SUPPORTED  = list(_BUILDERS.keys())


def build_dicom(modality: str, params: dict, patient: dict) -> list:
    """Return list[bytes] — one DICOM file per element (multi for CT/MR)."""
    mod = modality.upper()
    if mod not in _BUILDERS:
        raise ValueError(f"Modality '{mod}' not supported. Available: {SUPPORTED}")
    return _BUILDERS[mod](patient, params)
