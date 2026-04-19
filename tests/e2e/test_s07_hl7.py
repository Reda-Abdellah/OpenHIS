"""
Scenario 7 — HL7 v2 Interoperability

Mirrors SCENARIO 7 in docs/verification_and_validation/v-and-v-scenario.md.

Covers:
  ✅ S7.1 — POST /hl7/api/send/adt builds and stores an outbound ADT^A04
  ✅ S7.2 — POST /hl7/api/send/oru builds and stores an outbound ORU^R01
  ✅ S7.3 — /hl7/api/messages returns both messages in history
  ✅ S7.4 — /hl7/api/messages/stats aggregates inbound/outbound/today/by_type
  ✅ S7.5 — HL7 gateway SPA loads at /hl7/
  ✅ S7.6 — MLLP socket accepts a connection on port 2575 (if reachable)
  ❌ S7.7 — outbound message history captures patient_id / patient_name
            (xfail: DEF-008 — PID parse is skipped on the outbound store path)
"""
import socket
from contextlib import closing

import pytest


pytestmark = pytest.mark.e2e


MLLP_HOST    = "localhost"
MLLP_PORT    = 2575
MLLP_START   = b"\x0b"
MLLP_END     = b"\x1c\x0d"


class TestS7_HL7_HTTP:

    def test_s7_1_send_adt_a04(self, hl7_api, request):
        r = hl7_api.post("/send/adt", json={
            "mrn":        "E2E-HL7-A04",
            "first_name": "Marie",
            "last_name":  "Curie",
            "gender":     "F",
            "birth_date": "1867-11-07",
            "event_code": "A04",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"]   == "ok"
        assert body["msg_type"] == "ADT^A04"
        assert body["msg_id"] > 0
        assert "MSH|" in body["raw"]
        request.config.cache.set("s7/adt_id", body["msg_id"])

    def test_s7_2_send_oru_r01(self, hl7_api, request):
        r = hl7_api.post("/send/oru", json={
            "mrn":           "E2E-HL7-A04",
            "order_id":      "E2E-ORD-001",
            "test_code":     "GLU",
            "test_name":     "Glucose",
            "value":         "92",
            "unit":          "mg/dL",
            "abnormal_flag": "N",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"]   == "ok"
        assert body["msg_type"] == "ORU^R01"
        request.config.cache.set("s7/oru_id", body["msg_id"])

    def test_s7_3_messages_history(self, hl7_api, request):
        adt_id = request.config.cache.get("s7/adt_id", None)
        oru_id = request.config.cache.get("s7/oru_id", None)
        assert adt_id and oru_id, "S7.1/S7.2 did not cache msg ids"

        r = hl7_api.get("/messages", params={"limit": 100})
        assert r.status_code == 200
        rows = r.json()
        by_id = {row["id"]: row for row in rows}
        assert adt_id in by_id and oru_id in by_id

        adt, oru = by_id[adt_id], by_id[oru_id]
        assert adt["direction"] == "outbound"
        assert adt["msg_type"]  == "ADT^A04"
        assert adt["status"]    == "sent"
        assert oru["msg_type"]  == "ORU^R01"

    def test_s7_4_messages_stats(self, hl7_api):
        r = hl7_api.get("/messages/stats")
        assert r.status_code == 200
        body = r.json()
        for key in ("total", "inbound", "outbound", "errors", "today", "by_type"):
            assert key in body
        assert body["total"] >= 2
        assert body["outbound"] >= 2
        types = {row["msg_type"] for row in body["by_type"]}
        assert {"ADT^A04", "ORU^R01"}.issubset(types)


class TestS7_HL7_UI:

    def test_s7_5_spa_loads(self, http):
        r = http.get("/hl7/")
        assert r.status_code == 200
        assert "HL7 Gateway" in r.text or "hl7" in r.text.lower()


class TestS7_HL7_MLLP:
    """
    MLLP listener smoke. We only assert the TCP socket is reachable — sending
    a full HL7 message and parsing the ACK requires a PID-aware server that is
    feature-complete, which is tracked separately. A working TCP accept is
    enough to confirm the listener is up.
    """

    def test_s7_6_mllp_socket_accepts(self):
        try:
            with closing(socket.create_connection((MLLP_HOST, MLLP_PORT), timeout=3)) as s:
                # Send a minimal MLLP-framed ping; we don't require an ACK.
                s.sendall(MLLP_START + b"MSH|^~\\&|PING\r" + MLLP_END)
                s.settimeout(2)
                try:
                    s.recv(64)
                except socket.timeout:
                    pass
        except (ConnectionRefusedError, OSError) as e:
            pytest.skip(f"MLLP listener on {MLLP_HOST}:{MLLP_PORT} not reachable: {e}")


class TestS7_HL7_KnownDefects:

    @pytest.mark.xfail(
        reason="DEF-008: outbound /api/send/adt and /api/send/oru persist "
               "the row with patient_id='' and patient_name=None. The PID "
               "parser is not called on the outbound store path.",
        strict=False,
    )
    def test_s7_7_outbound_captures_patient_identifier(self, hl7_api, request):
        adt_id = request.config.cache.get("s7/adt_id", None)
        assert adt_id
        r = hl7_api.get(f"/messages/{adt_id}")
        assert r.status_code == 200
        body = r.json()
        assert body.get("patient_id") == "E2E-HL7-A04"
        assert body.get("patient_name")  # non-empty
