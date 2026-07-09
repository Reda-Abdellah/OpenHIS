"""MLLP outbound client — send an HL7 v2 message to a remote MLLP listener.

Lightweight asyncio sender complementing ``mllp.py`` (which is the
inbound server side). Used by ``/api/send/orm`` to transmit order
messages to a downstream MLLP listener when ``ORM_MLLP_HOST`` is set.
"""
from __future__ import annotations

import asyncio
import logging

from mllp import wrap, unwrap

log = logging.getLogger("hl7.mllp_client")


async def send_mllp(host: str, port: int, message: str,
                    *, timeout: float = 15.0) -> str:
    """Send ``message`` to ``host:port`` over MLLP and return the ACK.

    Raises ``asyncio.TimeoutError`` if the listener doesn't ACK
    within ``timeout`` seconds; raises ``OSError`` on connection
    failure. Closes the connection cleanly in all cases.
    """
    log.info("MLLP send → %s:%s (%d bytes)", host, port, len(message))
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout,
    )
    try:
        writer.write(wrap(message))
        await writer.drain()
        # Read until we see the MLLP end-of-frame (FS+CR).
        buf = b""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError("MLLP ACK timeout")
            chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
            if not chunk:
                break
            buf += chunk
            if b"\x1c" in buf:
                break
        ack = unwrap(buf) or ""
        log.info("MLLP ACK (%d bytes) head=%r", len(ack), ack[:60])
        return ack
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
