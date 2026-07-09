"""
Phase 6 — HL7 v2 Tests (30 tests)
Covers: parser, builder, MLLP framing, message API, send API, inbound API.
"""

# ── Sample messages ───────────────────────────────────────────────────────────
ADT_A01 = (
    "MSH|^~\\&|EHR|LOCAL|HL7-SVC|REMOTE|20260324120000||ADT^A01|CTRL001|P|2.5\r"
    "EVN|A01|20260324120000\r"
    "PID|1|P-001|MRN-001^^^MRN||Doe^Jane||19850315|F|||1 Main St^^Lyon^^69001|\r"
    "PV1|1|I|ICU^101^A||||DR-001^Smith^John||||||||||||ENC-001"
)

ADT_A03 = (
    "MSH|^~\\&|EHR|LOCAL|HL7-SVC|REMOTE|20260324130000||ADT^A03|CTRL002|P|2.5\r"
    "EVN|A03|20260324130000\r"
    "PID|1|P-001|MRN-001^^^MRN||Doe^Jane||19850315|F\r"
    "PV1|1|I|ICU^101^A||||||||||||||||ENC-001"
)

ADT_A04 = (
    "MSH|^~\\&|EHR|LOCAL|HL7-SVC|REMOTE|20260324110000||ADT^A04|CTRL003|P|2.5\r"
    "EVN|A04|20260324110000\r"
    "PID|1|P-002|MRN-002^^^MRN||Smith^Alice||19901215|F|||2 Rue de la Paix^^Lyon"
)

ORU_R01 = (
    "MSH|^~\\&|LIS|LOCAL|HL7-SVC|REMOTE|20260324140000||ORU^R01|CTRL004|P|2.5\r"
    "PID|1|P-001|MRN-001^^^MRN||Doe^Jane||19850315|F\r"
    "OBR|1|ORD-001||LAB|||20260324140000\r"
    "OBX|1|NM|Hemoglobin^^LN||13.2|g/dL|12.0-16.0||N|||F"
)

ACK_AA = (
    "MSH|^~\\&|HL7-SVC|LOCAL|EHR|REMOTE|20260324120001||ACK|CTRL999|P|2.5\r"
    "MSA|AA|CTRL001|Message accepted"
)


# ─────────────────────────────────────────────────────────────────────────────
# TestParser
# ─────────────────────────────────────────────────────────────────────────────
class TestParser:
    def test_parse_msg_type(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['msg_type'] == 'ADT^A01'

    def test_parse_control_id(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['control_id'] == 'CTRL001'

    def test_parse_sending_app(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['sending_app'] == 'EHR'

    def test_parse_pid_mrn(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['mrn'] == 'MRN-001'

    def test_parse_pid_name(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['lastname']  == 'Doe'
        assert p['firstname'] == 'Jane'

    def test_parse_pid_demographics(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['birthdate'] == '19850315'
        assert p['sex']       == 'F'

    def test_parse_pv1_location(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['ward'] == 'ICU'
        assert p['bed']  == 'A'

    def test_parse_pv1_visit_id(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['visit_id'] == 'ENC-001'

    def test_parse_pv1_patient_class(self):
        from parser import parse
        p = parse(ADT_A01)
        assert p['patient_class'] == 'I'

    def test_parse_msa_ack_code(self):
        from parser import parse
        p = parse(ACK_AA)
        assert p['ack_code']    == 'AA'
        assert p['ack_ctrl_id'] == 'CTRL001'

    def test_parse_oru_obr_order_id(self):
        from parser import parse
        p = parse(ORU_R01)
        assert p['order_id'] == 'ORD-001'

    def test_parse_newline_separator_fallback(self):
        from parser import parse
        msg = ADT_A01.replace('\r', '\n')
        p   = parse(msg)
        assert p['msg_type'] == 'ADT^A01'
        assert p['mrn']      == 'MRN-001'

    def test_parse_segments_list(self):
        from parser import parse
        p = parse(ADT_A01)
        assert 'MSH' in p['_segments']
        assert 'PID' in p['_segments']
        assert 'PV1' in p['_segments']

    def test_parse_missing_pv1_returns_empty(self):
        from parser import parse
        msg = ADT_A04   # no PV1
        p   = parse(msg)
        assert p['ward'] == ''
        assert p['bed']  == ''


# ─────────────────────────────────────────────────────────────────────────────
# TestBuilder
# ─────────────────────────────────────────────────────────────────────────────
PATIENT = {
    "id":        "P-001",
    "mrn":       "MRN-001",
    "firstname": "Jane",
    "lastname":  "Doe",
    "birthdate": "1985-03-15",
    "sex":       "F",
    "phone":     "555-0100",
}
ENCOUNTER = {"id": "ENC-001", "ward": "ICU", "bed": "101"}


class TestBuilder:
    def test_build_adt_a01_has_msh(self):
        from builder import build_adt
        msg = build_adt('A01', PATIENT, ENCOUNTER)
        assert msg.startswith('MSH|')

    def test_build_adt_a01_msg_type(self):
        from builder import build_adt
        from parser  import parse
        msg = build_adt('A01', PATIENT, ENCOUNTER)
        p   = parse(msg)
        assert p['msg_type'] == 'ADT^A01'

    def test_build_adt_a01_has_pid(self):
        from builder import build_adt
        msg = build_adt('A01', PATIENT, ENCOUNTER)
        assert '\rPID|' in msg or msg.startswith('PID|')
        assert 'MRN-001' in msg

    def test_build_adt_a01_has_pv1_when_encounter_given(self):
        from builder import build_adt
        msg = build_adt('A01', PATIENT, ENCOUNTER)
        assert 'PV1|' in msg
        assert 'ICU'   in msg

    def test_build_adt_a04_no_pv1_without_encounter(self):
        from builder import build_adt
        msg = build_adt('A04', PATIENT)
        assert 'PV1|' not in msg

    def test_build_adt_a03_correct_type(self):
        from builder import build_adt
        from parser  import parse
        msg = build_adt('A03', PATIENT, ENCOUNTER)
        p   = parse(msg)
        assert p['msg_type'] == 'ADT^A03'

    def test_build_adt_a40_has_mrg(self):
        from builder import build_adt_a40
        surviving = {**PATIENT}
        retired   = {"id": "P-OLD", "mrn": "MRN-OLD"}
        msg       = build_adt_a40(surviving, retired)
        assert 'ADT^A40' in msg
        assert 'MRG|'    in msg
        assert 'MRN-OLD' in msg

    def test_build_ack_aa(self):
        from builder import build_ack
        from parser  import parse
        msg = build_ack('CTRL001', 'AA', 'All good')
        p   = parse(msg)
        assert p['ack_code']    == 'AA'
        assert p['ack_ctrl_id'] == 'CTRL001'
        assert p['ack_text']    == 'All good'

    def test_build_ack_ae(self):
        from builder import build_ack
        from parser  import parse
        msg = build_ack('CTRL002', 'AE', 'Parse error')
        p   = parse(msg)
        assert p['ack_code'] == 'AE'

    def test_build_oru_r01_has_obx(self):
        from builder import build_oru_r01
        results = [
            {"analyte": "Hemoglobin", "value": "13.2",
             "unit": "g/dL", "referencerange": "12-16", "flag": "N"},
            {"analyte": "WBC",        "value": "7.1",
             "unit": "10^3/uL", "referencerange": "4-11",  "flag": "N"},
        ]
        msg = build_oru_r01(PATIENT, "ORD-001", results)
        assert 'ORU^R01'    in msg
        assert 'OBX|1|'     in msg
        assert 'OBX|2|'     in msg
        assert 'Hemoglobin' in msg

    def test_build_roundtrip(self):
        """Build → parse → field values survive round-trip."""
        from builder import build_adt
        from parser  import parse
        msg = build_adt('A08', PATIENT, ENCOUNTER)
        p   = parse(msg)
        assert p['mrn']       == 'MRN-001'
        assert p['lastname']  == 'Doe'
        assert p['firstname'] == 'Jane'

    def test_build_control_id_unique(self):
        from builder import build_adt
        from parser  import parse
        ids = {parse(build_adt('A04', PATIENT))['control_id'] for _ in range(10)}
        assert len(ids) > 1   # control IDs must differ across calls


# ─────────────────────────────────────────────────────────────────────────────
# TestMLLP
# ─────────────────────────────────────────────────────────────────────────────
class TestMLLP:
    def test_wrap_starts_with_vt(self):
        from mllp import wrap, MLLP_VT
        assert wrap("MSH|test")[0:1] == MLLP_VT

    def test_wrap_ends_with_fs_cr(self):
        from mllp import wrap, MLLP_FS, MLLP_CR
        data = wrap("MSH|test")
        assert data[-2:-1] == MLLP_FS
        assert data[-1:]   == MLLP_CR

    def test_unwrap_recovers_message(self):
        from mllp import wrap, unwrap
        original = "MSH|^~\\&|TEST|||||||ADT^A01|CTRL|P|2.5"
        assert unwrap(wrap(original)) == original

    def test_unwrap_returns_none_on_missing_vt(self):
        from mllp import unwrap, MLLP_FS, MLLP_CR
        assert unwrap(b"MSH|plain" + MLLP_FS + MLLP_CR) is None

    def test_unwrap_returns_none_on_empty(self):
        from mllp import unwrap
        assert unwrap(b"") is None

    def test_unwrap_returns_none_on_partial_frame(self):
        from mllp import unwrap, MLLP_VT
        assert unwrap(MLLP_VT + b"MSH|no-end") is None

    def test_wrap_unwrap_preserves_cr_segments(self):
        from mllp import wrap, unwrap
        msg = "MSH|field\rPID|1\rPV1|1"
        assert unwrap(wrap(msg)) == msg


# ─────────────────────────────────────────────────────────────────────────────
# TestMessageAPI
# ─────────────────────────────────────────────────────────────────────────────
class TestMessageAPI:
    def test_list_messages_empty(self, client):
        r = client.get("/api/messages")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_messages_after_send(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        r = client.get("/api/messages")
        assert len(r.json()) == 1

    def test_filter_by_direction_outbound(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        r = client.get("/api/messages?direction=outbound")
        assert all(m["direction"] == "outbound" for m in r.json())

    def test_filter_by_msg_type(self, client):
        client.post("/api/send/adt",
                    json={"event": "A01", "patient": PATIENT,
                          "encounter": ENCOUNTER})
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        r = client.get("/api/messages?msg_type=ADT^A01")
        assert all(m["msg_type"] == "ADT^A01" for m in r.json())

    def test_get_message_by_id(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        msgs = client.get("/api/messages").json()
        mid  = msgs[0]["id"]
        r    = client.get(f"/api/messages/{mid}")
        assert r.status_code == 200
        assert "raw" in r.json()
        assert r.json()["id"] == mid

    def test_get_message_404(self, client):
        r = client.get("/api/messages/99999")
        assert r.status_code == 404

    def test_stats_counts(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        client.post("/api/messages/inbound",
                    json={"raw": ADT_A01})
        r = client.get("/api/messages/stats")
        j = r.json()
        assert j["total"]    >= 2
        assert j["outbound"] >= 1
        assert j["inbound"]  >= 1

    def test_stats_by_type(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        r = client.get("/api/messages/stats")
        types = [x["msg_type"] for x in r.json()["by_type"]]
        assert "ADT^A04" in types


# ─────────────────────────────────────────────────────────────────────────────
# TestSendAPI
# ─────────────────────────────────────────────────────────────────────────────
class TestSendAPI:
    def test_send_adt_a04_returns_200(self, client):
        r = client.post("/api/send/adt",
                        json={"event": "A04", "patient": PATIENT})
        assert r.status_code == 200
        assert r.json()["msg_type"] == "ADT^A04"

    def test_send_adt_a01_with_encounter(self, client):
        r = client.post("/api/send/adt",
                        json={"event": "A01", "patient": PATIENT,
                              "encounter": ENCOUNTER})
        assert r.status_code == 200
        assert "PV1|" in r.json()["raw"]

    def test_send_adt_a03_discharge(self, client):
        r = client.post("/api/send/adt",
                        json={"event": "A03", "patient": PATIENT,
                              "encounter": ENCOUNTER})
        assert r.status_code == 200
        assert r.json()["msg_type"] == "ADT^A03"

    def test_send_adt_a40_merge(self, client):
        retired = {"id": "P-OLD", "mrn": "MRN-OLD",
                   "firstname": "Old", "lastname": "Record"}
        r = client.post("/api/send/adt",
                        json={"event": "A40", "patient": PATIENT,
                              "retired_patient": retired})
        assert r.status_code == 200
        assert "MRG|"    in r.json()["raw"]
        assert "MRN-OLD" in r.json()["raw"]

    def test_send_oru_r01(self, client):
        r = client.post("/api/send/oru", json={
            "patient":  PATIENT,
            "order_id": "ORD-TEST-001",
            "results":  [{"analyte": "K", "value": "4.2",
                          "unit": "mmol/L", "flag": "N"}],
        })
        assert r.status_code == 200
        assert r.json()["msg_type"] == "ORU^R01"
        assert "OBX|1|"  in r.json()["raw"]

    def test_send_invalid_event_returns_422(self, client):
        r = client.post("/api/send/adt",
                        json={"event": "A99", "patient": PATIENT})
        assert r.status_code == 422

    def test_send_logged_to_db(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        msgs = client.get("/api/messages?direction=outbound").json()
        assert len(msgs) >= 1
        assert msgs[0]["status"] == "sent"


# ─────────────────────────────────────────────────────────────────────────────
# TestOutboundPatientPersistence (DEF-008)
# Outbound messages must persist patient_id / patient_name via the shared
# PID parser, for both nested {"patient": {...}} and flat portal/e2e bodies.
# ─────────────────────────────────────────────────────────────────────────────
class TestOutboundPatientPersistence:
    def test_adt_nested_patient_persists_id_and_name(self, client):
        r = client.post("/api/send/adt",
                        json={"event": "A04", "patient": PATIENT})
        assert r.status_code == 200
        msg = client.get(f"/api/messages/{r.json()['msg_id']}").json()
        assert msg["patient_id"]   == "MRN-001"
        assert msg["patient_name"] == "Jane Doe"

    def test_adt_flat_body_persists_id_and_name(self, client):
        r = client.post("/api/send/adt", json={
            "mrn":        "E2E-HL7-A04",
            "first_name": "Marie",
            "last_name":  "Curie",
            "gender":     "F",
            "birth_date": "1867-11-07",
            "event_code": "A04",
        })
        assert r.status_code == 200
        assert r.json()["msg_type"] == "ADT^A04"
        msg = client.get(f"/api/messages/{r.json()['msg_id']}").json()
        assert msg["patient_id"] == "E2E-HL7-A04"
        assert msg["patient_name"]          # non-empty

    def test_oru_flat_body_persists_id_and_name(self, client):
        r = client.post("/api/send/oru", json={
            "mrn":           "E2E-HL7-A04",
            "first_name":    "Marie",
            "last_name":     "Curie",
            "order_id":      "E2E-ORD-001",
            "test_code":     "GLU",
            "test_name":     "Glucose",
            "value":         "92",
            "unit":          "mg/dL",
            "abnormal_flag": "N",
        })
        assert r.status_code == 200
        assert "OBX|1|" in r.json()["raw"]
        msg = client.get(f"/api/messages/{r.json()['msg_id']}").json()
        assert msg["patient_id"] == "E2E-HL7-A04"
        assert msg["patient_name"]          # non-empty

    def test_orm_persists_patient_id(self, client):
        r = client.post("/api/send/orm", json={
            "patient":  PATIENT,
            "order_id": "ORD-ORM-001",
            "tests":    [{"loinc": "2345-7", "name": "Glucose"}],
            "transmit": False,
        })
        assert r.status_code == 200
        msg = client.get(f"/api/messages/{r.json()['msg_id']}").json()
        assert msg["patient_id"]   == "MRN-001"
        assert msg["patient_name"] == "Jane Doe"

    def test_adt_without_patient_stores_empty_without_error(self, client):
        r = client.post("/api/send/adt", json={"event": "A04"})
        assert r.status_code == 200
        msg = client.get(f"/api/messages/{r.json()['msg_id']}").json()
        assert msg["patient_id"] in ("", None)
        assert msg["patient_name"] is None


# ─────────────────────────────────────────────────────────────────────────────
# TestInboundAPI
# ─────────────────────────────────────────────────────────────────────────────
class TestInboundAPI:
    def test_inbound_returns_202(self, client):
        r = client.post("/api/messages/inbound", json={"raw": ADT_A01})
        assert r.status_code == 202

    def test_inbound_response_has_ack(self, client):
        r = client.post("/api/messages/inbound", json={"raw": ADT_A01})
        j = r.json()
        assert "ack"      in j
        assert "MSA|AA|"  in j["ack"]

    def test_inbound_response_has_msg_id(self, client):
        r = client.post("/api/messages/inbound", json={"raw": ADT_A01})
        assert r.json()["msg_id"] > 0

    def test_inbound_logged_as_inbound(self, client):
        client.post("/api/messages/inbound", json={"raw": ADT_A04})
        msgs = client.get("/api/messages?direction=inbound").json()
        assert len(msgs) >= 1
        assert msgs[0]["direction"] == "inbound"

    def test_inbound_missing_raw_returns_400(self, client):
        r = client.post("/api/messages/inbound", json={})
        assert r.status_code == 400

    def test_inbound_oru_accepted(self, client):
        r = client.post("/api/messages/inbound", json={"raw": ORU_R01})
        assert r.status_code == 202
        assert r.json()["msg_type"] == "ORU^R01"


# ─────────────────────────────────────────────────────────────────────────────
# TestHealth
# ─────────────────────────────────────────────────────────────────────────────
class TestHealthHL7:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        j = r.json()
        assert j["status"]  == "ok"
        assert j["service"] == "hl7"

    def test_health_mllp_disabled_in_test(self, client):
        r = client.get("/api/health")
        assert r.json()["mllp_enabled"] is False

    def test_health_counts_messages(self, client):
        client.post("/api/send/adt",
                    json={"event": "A04", "patient": PATIENT})
        client.post("/api/messages/inbound", json={"raw": ADT_A01})
        r = client.get("/api/health")
        j = r.json()
        assert j["total"]    >= 2
        assert j["outbound"] >= 1
        assert j["inbound"]  >= 1
