from fastapi import APIRouter, Query
from app.db.audit import query_events
import app.worker as worker

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def get_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: str = Query(""),
    resource_type: str = Query(""),
):
    """Return paginated audit log entries, newest first."""
    events = await query_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        resource_type=resource_type,
    )
    return {
        "count": len(events),
        "retry_queue_depth": len(worker._retry_queue),
        "events": events,
    }
