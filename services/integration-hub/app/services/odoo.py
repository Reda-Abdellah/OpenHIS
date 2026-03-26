"""
Odoo XML-RPC client.

Uses the standard xmlrpc.client (sync) wrapped in asyncio.to_thread so it
doesn't block the event loop.
"""
import asyncio
import logging
import xmlrpc.client
from typing import Optional
from app.config import ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASS

log = logging.getLogger("hub.odoo")


def _common() -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")


def _models() -> xmlrpc.client.ServerProxy:
    return xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")


def _version() -> dict:
    return _common().version()


def _authenticate() -> Optional[int]:
    try:
        uid = _common().authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
        return uid if uid else None
    except Exception as e:
        log.warning(f"Odoo authenticate: {e}")
        return None


def health_check() -> bool:
    """Synchronous: return True if Odoo version() call succeeds."""
    try:
        v = _version()
        return bool(v.get("server_version"))
    except Exception:
        return False


async def async_health_check() -> bool:
    return await asyncio.to_thread(health_check)


def _create_pharmacy_order_sync(patient_name: str, drug_name: str,
                                quantity: float, notes: str) -> Optional[int]:
    uid = _authenticate()
    if not uid:
        return None
    models = _models()

    # Find or create patient partner
    partners = models.execute_kw(ODOO_DB, uid, ODOO_PASS,
        "res.partner", "search", [[["name", "=", patient_name]]])
    partner_id = partners[0] if partners else models.execute_kw(
        ODOO_DB, uid, ODOO_PASS, "res.partner", "create",
        [{"name": patient_name, "is_company": False}])

    # Find drug product (best-effort — order still created if not found)
    products = models.execute_kw(ODOO_DB, uid, ODOO_PASS,
        "product.product", "search", [[["name", "ilike", drug_name]]])

    order_vals: dict = {"partner_id": partner_id, "note": notes}
    if products:
        order_vals["order_line"] = [(0, 0, {
            "product_id": products[0],
            "product_uom_qty": quantity,
        })]

    order_id = models.execute_kw(ODOO_DB, uid, ODOO_PASS,
        "sale.order", "create", [order_vals])
    log.info(f"Odoo sale.order/{order_id} — {patient_name} / {drug_name}")
    return order_id


async def create_pharmacy_order(patient_name: str, drug_name: str,
                                quantity: float = 1.0,
                                notes: str = "") -> Optional[int]:
    """Async wrapper — create a sale.order in Odoo for a pharmacy dispensing request."""
    try:
        return await asyncio.to_thread(
            _create_pharmacy_order_sync, patient_name, drug_name, quantity, notes)
    except Exception as e:
        log.warning(f"create_pharmacy_order: {e}")
        return None
