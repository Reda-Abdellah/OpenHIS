"""
MLLP TCP server tests.

Tests the actual asyncio-based TCP MLLP server by:
- Spinning up a server on a random loopback port
- Connecting as a client and sending MLLP-framed HL7 messages
- Verifying ACK responses

These tests complement the existing unit tests for the parser/builder
by exercising the actual network transport layer that was previously
always disabled in tests (MLLP_ENABLED=false).
"""
import asyncio, os, sys, pytest
from pathlib import Path

HL7_SERVICE = str(Path(__file__).parent.parent.parent / "services" / "hl7")
if HL7_SERVICE not in sys.path:
    sys.path.insert(0, HL7_SERVICE)

# ── MLLP framing constants ────────────────────────────────────────────────────
VT = b'\x0b'    # start of block
FS = b'\x1c'    # end of block
CR = b'\x0d'    # end of transmission


def _wrap(msg: str) -> bytes:
    return VT + msg.encode('utf-8') + FS + CR


def _unwrap(data: bytes) -> str | None:
    s = data.find(VT)
    e = data.find(FS, s + 1 if s >= 0 else 0)
    if s == -1 or e == -1 or e <= s:
        return None
    return data[s + 1:e].decode('utf-8', errors='replace')


# ── sample HL7 messages ───────────────────────────────────────────────────────

ADT_A01 = (
    "MSH|^~\\&|EHR|LOCAL|HL7SVC|REMOTE|20260325120000||ADT^A01|12345|P|2.5\r"
    "EVN|A01|20260325120000\r"
    "PID|1|P001|MRN001^^^MRN||SMITH^JOHN||19800101|M|||123 MAIN ST||555-1234\r"
    "PV1|1|I|ICU^A^01||||||||||||||||V001\r"
)

ADT_A03 = (
    "MSH|^~\\&|EHR|LOCAL|HL7SVC|REMOTE|20260325130000||ADT^A03|12346|P|2.5\r"
    "EVN|A03|20260325130000\r"
    "PID|1|P001|MRN001^^^MRN||SMITH^JOHN||19800101|M\r"
    "PV1|1|I|ICU^A^01||||||||||||||||V001\r"
)

ORU_R01 = (
    "MSH|^~\\&|LIS|LOCAL|HL7SVC|REMOTE|20260325140000||ORU^R01|12347|P|2.5\r"
    "PID|1|P001|MRN001^^^MRN||SMITH^JOHN||19800101|M\r"
    "OBR|1|LAB001||CBC|||20260325140000\r"
    "OBX|1|NM|WBC^^LN||7.5|K/uL|4.5-11.0||N|||F\r"
)

MALFORMED = "NOT A VALID HL7 MESSAGE AT ALL"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def hl7_db(tmp_path, monkeypatch):
    """Fresh HL7 database."""
    db = str(tmp_path / "hl7_mllp.db")
    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("EHR_URL", "http://localhost:19999/api")  # won't be called
    monkeypatch.setenv("MPI_URL", "http://localhost:19999/api")

    # Reload HL7 modules fresh
    to_clear = [k for k in sys.modules
                if k in ('main', 'database', 'handlers', 'mllp', 'parser', 'builder')
                or k.startswith('routers.')]
    for mod in to_clear:
        del sys.modules[mod]
    if HL7_SERVICE in sys.path:
        sys.path.remove(HL7_SERVICE)
    sys.path.insert(0, HL7_SERVICE)

    from database import init_db
    init_db()


@pytest.fixture
def mllp_server(hl7_db, unused_tcp_port):
    """Start a real MLLP TCP server on a random port."""
    async def _start():
        from mllp import start_server
        from handlers import dispatch
        server = await start_server("127.0.0.1", unused_tcp_port, dispatch)
        return server

    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(_start())
    yield unused_tcp_port, loop
    server.close()
    loop.run_until_complete(server.wait_closed())
    loop.close()


@pytest.fixture
def unused_tcp_port():
    """Find a free TCP port."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── helpers ───────────────────────────────────────────────────────────────────

async def _send_and_receive(host: str, port: int, msg: str,
                             timeout: float = 5.0) -> str:
    """Open a TCP connection, send an MLLP-framed message, wait for ACK."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(_wrap(msg))
        await writer.drain()
        buf = b''
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                pytest.fail("Timed out waiting for MLLP ACK")
            chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
            if not chunk:
                break
            buf += chunk
            if FS in buf:
                break
        return _unwrap(buf) or ""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _run(coro, loop):
    return loop.run_until_complete(coro)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestMLLPFraming:
    """Unit tests for MLLP wrap/unwrap — no network needed."""

    def test_wrap_adds_framing(self):
        raw = "MSH|test message"
        framed = _wrap(raw)
        assert framed[0:1] == VT
        assert framed[-2:-1] == FS
        assert framed[-1:] == CR

    def test_unwrap_extracts_message(self):
        raw = "MSH|test message"
        extracted = _unwrap(_wrap(raw))
        assert extracted == raw

    def test_unwrap_returns_none_for_missing_start(self):
        data = b"MSH|test message" + FS + CR
        assert _unwrap(data) is None

    def test_unwrap_returns_none_for_missing_end(self):
        data = VT + b"MSH|test message"
        assert _unwrap(data) is None

    def test_unwrap_ignores_content_after_fs(self):
        raw = "MSH|hello"
        framed = _wrap(raw) + b"GARBAGE"
        assert _unwrap(framed) == raw

    def test_wrap_unwrap_round_trip_with_cr_in_message(self):
        """HL7 uses \r as segment separator — must survive round-trip."""
        msg = "MSH|^~\\&|A|B|||20260101||ADT^A01|1|P|2.5\rEVN|A01\rPID|1||P001\r"
        assert _unwrap(_wrap(msg)) == msg


class TestMLLPServerRespondsToADT:
    """Test that the live MLLP server accepts HL7 messages and returns ACK."""

    def test_adt_a01_returns_aa_ack(self, mllp_server):
        port, loop = mllp_server
        # Dispatch calls EHR/MPI URLs — they're unreachable but errors are swallowed
        import respx, httpx
        with respx.mock:
            respx.route(method="POST", url__regex=r"http://localhost:19999/.*").mock(
                return_value=httpx.Response(200, json={})
            )
            ack = _run(_send_and_receive("127.0.0.1", port, ADT_A01), loop)

        assert "MSA" in ack, f"Expected MSA segment in ACK, got: {ack!r}"
        # AA = Application Acknowledged
        assert "|AA|" in ack, f"Expected AA ack code, got: {ack!r}"

    def test_adt_a03_returns_aa_ack(self, mllp_server):
        port, loop = mllp_server
        import respx, httpx
        with respx.mock:
            respx.route(method="PATCH", url__regex=r"http://localhost:19999/.*").mock(
                return_value=httpx.Response(200, json={})
            )
            ack = _run(_send_and_receive("127.0.0.1", port, ADT_A03), loop)

        assert "MSA" in ack
        assert "|AA|" in ack

    def test_oru_r01_returns_aa_ack(self, mllp_server):
        port, loop = mllp_server
        import respx, httpx
        with respx.mock:
            respx.route(method="POST", url__regex=r"http://localhost:19999/.*").mock(
                return_value=httpx.Response(200, json={})
            )
            ack = _run(_send_and_receive("127.0.0.1", port, ORU_R01), loop)

        assert "MSA" in ack
        assert "|AA|" in ack

    def test_malformed_message_returns_ae_ack(self, mllp_server):
        port, loop = mllp_server
        ack = _run(_send_and_receive("127.0.0.1", port, MALFORMED), loop)
        assert "MSA" in ack
        assert "|AE|" in ack, f"Expected AE (error) ack for malformed message, got: {ack!r}"

    def test_ack_control_id_matches_request(self, mllp_server):
        """ACK MSA segment must echo back the control ID from MSH.10."""
        port, loop = mllp_server
        import respx, httpx
        with respx.mock:
            respx.route(method="POST", url__regex=r"http://localhost:19999/.*").mock(
                return_value=httpx.Response(200, json={})
            )
            ack = _run(_send_and_receive("127.0.0.1", port, ADT_A01), loop)

        # Control ID from ADT_A01 is 12345
        assert "12345" in ack, f"ACK must echo control ID 12345, got: {ack!r}"

    def test_multiple_messages_on_same_connection(self, mllp_server):
        """Server must handle multiple sequential messages on one TCP connection."""
        port, loop = mllp_server

        async def _multi():
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            acks = []
            try:
                for msg in [ADT_A01, ADT_A03]:
                    writer.write(_wrap(msg))
                    await writer.drain()
                    buf = b''
                    while FS not in buf:
                        buf += await asyncio.wait_for(reader.read(4096), timeout=5)
                    acks.append(_unwrap(buf))
            finally:
                writer.close()
            return acks

        import respx, httpx
        with respx.mock:
            respx.route().mock(return_value=httpx.Response(200, json={}))
            acks = _run(_multi(), loop)

        assert len(acks) == 2
        for ack in acks:
            assert "MSA" in ack
            assert "|AA|" in ack


class TestMLLPServerConcurrency:
    """Test that the MLLP server handles concurrent connections."""

    def test_concurrent_connections(self, mllp_server):
        port, loop = mllp_server

        async def _concurrent():
            import respx, httpx
            with respx.mock:
                respx.route().mock(return_value=httpx.Response(200, json={}))
                tasks = [
                    _send_and_receive("127.0.0.1", port, ADT_A01)
                    for _ in range(5)
                ]
                return await asyncio.gather(*tasks)

        acks = _run(_concurrent(), loop)
        assert len(acks) == 5
        for ack in acks:
            assert "MSA" in ack


class TestMLLPBuilderAndDispatch:
    """Test the build_adt helper + dispatch round-trip (no network)."""

    def test_build_adt_a01(self, hl7_db):
        from builder import build_adt
        msg = build_adt("A01", {
            "mrn": "TEST001", "firstname": "Alice", "lastname": "Smith",
            "birthdate": "1990-01-01", "sex": "F"
        }, {"id": "V001", "ward": "ICU", "encountertype": "inpatient"})
        assert "ADT^A01" in msg
        assert "TEST001" in msg
        assert "SMITH^ALICE" in msg.upper() or "Smith^Alice" in msg or "smith^alice" in msg.lower()

    def test_dispatch_unknown_message_still_acks(self, hl7_db):
        from handlers import dispatch

        unknown_msg = (
            "MSH|^~\\&|SRC|LOCAL|DEST|REMOTE|20260101||ZZZ^Z99|99999|P|2.5\r"
            "ZZZ|custom segment\r"
        )
        ack = loop = None
        loop = asyncio.new_event_loop()
        try:
            ack = loop.run_until_complete(dispatch(unknown_msg))
        finally:
            loop.close()

        assert ack is not None
        assert "MSA" in ack
        assert "99999" in ack  # control ID echoed back

    def test_dispatch_returns_ae_for_completely_broken_message(self, hl7_db):
        from handlers import dispatch
        loop = asyncio.new_event_loop()
        try:
            ack = loop.run_until_complete(dispatch("GARBAGE NOT HL7"))
        finally:
            loop.close()
        assert "AE" in ack
