"""
MLLP (Minimal Lower Layer Protocol) — RFC / HL7 Appendix C transport.
Frame: \x0B  <HL7 message>  \x1C\x0D
"""
import asyncio
import logging

log = logging.getLogger('hl7.mllp')

MLLP_VT = b'\x0b'   # Vertical Tab — start of block
MLLP_FS = b'\x1c'   # File Separator — end of block
MLLP_CR = b'\x0d'   # Carriage Return — end of transmission


def wrap(msg: str) -> bytes:
    """Wrap an HL7 message in MLLP framing."""
    return MLLP_VT + msg.encode('utf-8') + MLLP_FS + MLLP_CR


def unwrap(data: bytes) -> str | None:
    """
    Extract the HL7 message from MLLP-framed bytes.
    Returns None if framing is incomplete or missing.
    """
    s = data.find(MLLP_VT)
    e = data.find(MLLP_FS, s + 1 if s >= 0 else 0)
    if s == -1 or e == -1 or e <= s:
        return None
    return data[s + 1:e].decode('utf-8', errors='replace')


async def _handle_client(reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter,
                          dispatch_fn) -> None:
    addr = writer.get_extra_info('peername')
    log.info(f"MLLP connection from {addr}")
    buf = b''
    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(8192), timeout=120)
            if not chunk:
                break
            buf += chunk
            while MLLP_VT in buf and MLLP_FS in buf:
                s = buf.find(MLLP_VT)
                e = buf.find(MLLP_FS, s)
                if e == -1:
                    break
                raw  = buf[s + 1:e].decode('utf-8', errors='replace')
                buf  = buf[e + 2:]   # skip FS + CR
                try:
                    ack = await dispatch_fn(raw)
                except Exception as exc:
                    from builder import build_ack
                    ack = build_ack('UNKNOWN', 'AE', str(exc)[:80])
                writer.write(wrap(ack))
                await writer.drain()
    except asyncio.TimeoutError:
        log.debug(f"MLLP timeout from {addr}")
    except Exception as exc:
        log.warning(f"MLLP error from {addr}: {exc}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
    log.info(f"MLLP connection closed from {addr}")


async def start_server(host: str, port: int, dispatch_fn):
    """Start the asyncio MLLP TCP server. Returns the server object."""
    server = await asyncio.start_server(
        lambda r, w: _handle_client(r, w, dispatch_fn),
        host, port
    )
    log.info(f"MLLP server listening on {host}:{port}")
    return server
