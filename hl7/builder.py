"""
HL7 v2 message builder.
Segments are joined with \r (carriage return — HL7 spec standard).
"""
import datetime
import uuid

SENDING_APP = 'HL7-SVC'
SENDING_FAC = 'LOCAL'


def _ts() -> str:
    return datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')


def _ctrl() -> str:
    return str(uuid.uuid4().int)[:10]


def _msh(msg_type: str, sending_app: str = SENDING_APP,
         receiving_app: str = 'UNKNOWN') -> str:
    return (f"MSH|^~\\&|{sending_app}|{SENDING_FAC}"
            f"|{receiving_app}|REMOTE|{_ts()}||{msg_type}|{_ctrl()}|P|2.5")


def _evn(event_code: str) -> str:
    return f"EVN|{event_code}|{_ts()}"


def _pid(patient: dict) -> str:
    mrn       = patient.get('mrn', '')
    pid_field = patient.get('id', '')
    last      = patient.get('lastname', '')
    first     = patient.get('firstname', '')
    dob       = (patient.get('birthdate') or '').replace('-', '')
    sex       = (patient.get('sex') or '').upper()[:1]
    phone     = patient.get('phone', '')
    addr      = patient.get('address', '')
    return f"PID|1|{pid_field}|{mrn}^^^MRN||{last}^{first}||{dob}|{sex}|||{addr}||{phone}"


def _pv1(encounter: dict, event: str) -> str:
    ward  = encounter.get('ward', '')
    room  = encounter.get('room', '') if encounter.get('room') else ''
    bed   = encounter.get('bed', '')
    eid   = encounter.get('id', '')
    etype = 'I' if (encounter.get('encountertype') or '') == 'inpatient' else 'O'
    return f"PV1|1|{etype}|{ward}^{room}^{bed}||||||||||||||||{eid}"


def build_adt(event: str, patient: dict,
              encounter: dict = None,
              sending_app: str = 'EHR') -> str:
    """
    Build an ADT^Axx message.
    event: 'A01' | 'A02' | 'A03' | 'A04' | 'A08' | 'A11' | 'A40'
    """
    msg_type = f"ADT^A{event.lstrip('A').zfill(2)}" if not event.startswith('ADT') else event
    short    = msg_type.split('^')[1] if '^' in msg_type else event
    segs     = [_msh(msg_type, sending_app), _evn(short), _pid(patient)]
    if encounter:
        segs.append(_pv1(encounter, short))
    return '\r'.join(segs) + '\r'


def build_adt_a40(surviving: dict, retired: dict) -> str:
    """ADT^A40 — Merge Patients (retired → surviving)."""
    segs = [
        _msh('ADT^A40', 'MPI'),
        _evn('A40'),
        _pid(surviving),
        f"MRG|{retired.get('mrn', '')}^^^MRN||{retired.get('id', '')}",
    ]
    return '\r'.join(segs) + '\r'


def build_ack(control_id: str, ack_code: str = 'AA',
              text: str = 'Message accepted',
              sending_app: str = SENDING_APP) -> str:
    """Build an ACK message. ack_code: AA | AE | AR"""
    segs = [
        _msh('ACK', sending_app),
        f"MSA|{ack_code}|{control_id}|{text}",
    ]
    return '\r'.join(segs) + '\r'


def build_oru_r01(patient: dict, order_id: str,
                  results: list, sending_app: str = 'LIS') -> str:
    """
    Build ORU^R01 (Observation Result).
    results: list of {analyte, value, unit, flag, referencerange}
    """
    segs = [
        _msh('ORU^R01', sending_app),
        _pid(patient),
        f"OBR|1|{order_id}||LAB|||{_ts()}",
    ]
    for i, r in enumerate(results, 1):
        analyte  = r.get('analyte', '')
        value    = r.get('value', '')
        unit     = r.get('unit', '')
        flag     = r.get('flag', 'N')
        refrange = r.get('referencerange', '')
        segs.append(
            f"OBX|{i}|NM|{analyte}^^LN||{value}|{unit}|{refrange}||{flag}|||F"
        )
    return '\r'.join(segs) + '\r'
