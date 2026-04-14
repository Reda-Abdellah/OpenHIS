"""
MPI sync router.

Patient identity is now driven entirely by the Redis event bus:
  integration-hub  →  patient.registered  →  MPI bus_consumer  →  patient.synced

The legacy HTTP sync endpoints (/from-ehr, /from-lis, /from-ris) that called
into frozen legacy services have been removed. All inbound sync goes through
bus_consumer.py.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/sync", tags=["sync"])
