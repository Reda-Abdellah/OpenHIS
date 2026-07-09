from fastapi import APIRouter, BackgroundTasks, Depends
from openhis_sdk.auth import require_roles
import app.state as state
from app.worker import poll_once

router = APIRouter(prefix="/api/atomfeed", tags=["atomfeed"])


@router.get("/status")
def feed_status() -> dict:
    """Return cumulative sync counters and last poll timestamp."""
    return {
        "patients_synced": state.patients_synced,
        "orders_synced":   state.orders_synced,
        "reports_synced":  state.reports_synced,
        "errors":          state.errors,
        "last_poll_at":    state.last_poll_at or "never",
    }


@router.post("/trigger", dependencies=[Depends(require_roles("admin"))])
async def trigger_poll(bg: BackgroundTasks) -> dict:
    """Manually trigger a sync cycle (runs in background). Admin-only (T-06)."""
    bg.add_task(poll_once)
    return {"status": "triggered"}
