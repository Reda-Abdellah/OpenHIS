"""
HL7 v2 pipe-delimited message parser.
Handles segment separator \r (spec) with \n fallback.
Returns a flat dict of the most-used fields.
"""


def _field(seg: list, idx: int, default: str = '') -> str:
    try:
        return seg[idx] if idx < len(seg) else default
    except Exception:
        return default


def _comp(field: str, idx: int, default: str = '') -> str:
    parts = (field or '').split('^')
    try:
        return parts[idx] if idx < len(parts) else default
    except Exception:
        return default


def _rep(field: str, idx: int, default: str = '') -> str:
    """Return repetition idx from a ~ separated field."""
    parts = (field or '').split('~')
    try:
        return parts[idx] if idx < len(parts) else default
    except Exception:
        return default


def parse(raw: str) -> dict:
    """Parse a raw HL7 v2 message string into a flat dict."""
    # Normalise segment separator
    text = raw.replace('\r\n', '\r').replace('\n', '\r').strip()
    lines = [l for l in text.split('\r') if l.strip()]

    segments: dict[str, list] = {}
    for line in lines:
        name = line[:3].upper()
        if name == 'MSH':
            # MSH is special: field sep IS the 4th char
            fields = ['MSH', line[3]] + line[4:].split('|')
        else:
            fields = line.split('|')
        # Keep only first occurrence of each segment type
        if name not in segments:
            segments[name] = fields

    msh = segments.get('MSH', [])
    pid = segments.get('PID', [])
    pv1 = segments.get('PV1', [])
    msa = segments.get('MSA', [])
    obr = segments.get('OBR', [])
    mrg = segments.get('MRG', [])

    # MSH
    msg_type    = _field(msh, 9)
    control_id  = _field(msh, 10)
    sending_app = _field(msh, 3)
    recv_app    = _field(msh, 5)
    msg_dt      = _field(msh, 7)

    # PID
    pid3        = _field(pid, 3)          # patient identifier list
    mrn         = _comp(pid3, 0)          # first component = MRN
    name_raw    = _field(pid, 5)
    lastname    = _comp(name_raw, 0)
    firstname   = _comp(name_raw, 1)
    birthdate   = _field(pid, 7)
    sex         = _field(pid, 8)
    address     = _field(pid, 11)
    phone       = _field(pid, 13)
    ssn         = _field(pid, 19)

    # PV1
    patient_class = _field(pv1, 2)        # I=inpatient O=outpatient E=emergency
    location      = _field(pv1, 3)
    ward          = _comp(location, 0)
    room          = _comp(location, 1)
    bed           = _comp(location, 2)
    attending_raw = _field(pv1, 7)
    attending     = f"{_comp(attending_raw, 1)} {_comp(attending_raw, 0)}".strip()
    visit_id      = _field(pv1, 19)
    admit_type    = _field(pv1, 4)

    # MSA (ACK)
    ack_code    = _field(msa, 1)
    ack_ctrl_id = _field(msa, 2)
    ack_text    = _field(msa, 3)

    # OBR (ORU)
    order_id    = _field(obr, 2)

    # MRG (A40 patient merge — deprecated/retired patient identifiers)
    mrg_mrn     = _comp(_field(mrg, 1), 0)

    patient_name = f"{firstname} {lastname}".strip() or None

    return {
        "msg_type":     msg_type,
        "control_id":   control_id,
        "sending_app":  sending_app,
        "receiving_app": recv_app,
        "msg_datetime": msg_dt,
        "mrn":          mrn,
        "patient_name": patient_name,
        "firstname":    firstname,
        "lastname":     lastname,
        "birthdate":    birthdate,
        "sex":          sex,
        "address":      address,
        "phone":        phone,
        "ssn":          ssn,
        "patient_class": patient_class,
        "ward":         ward,
        "room":         room,
        "bed":          bed,
        "attending":    attending,
        "visit_id":     visit_id,
        "admit_type":   admit_type,
        "ack_code":     ack_code,
        "ack_ctrl_id":  ack_ctrl_id,
        "ack_text":     ack_text,
        "order_id":     order_id,
        "mrg_mrn":      mrg_mrn,
        "_segments":    list(segments.keys()),
    }
