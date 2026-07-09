"""
Minimal FHIR R4 façade over the MPI (pragmatic PDQm/PIXm flavour).

Endpoints
---------
GET /fhir/Patient
    Search by ``identifier=<system>|<value>`` token (bare value matches the
    master MRN) or by ``family`` + ``given`` + ``birthdate`` demographics.
    Always returns a ``searchset`` Bundle — empty (total=0) on no match.

GET /fhir/Patient/$ihe-pix
    IHE PIXm-style cross-reference query: ``sourceIdentifier=<system>|<value>``
    resolves to a master record and returns a Parameters resource listing all
    *other* identifiers known for that patient (optionally narrowed by
    ``targetSystem``), plus a ``targetId`` reference to the master record.

Identifier systems
------------------
The master MRN is published under ``MPI_SYSTEM`` (env ``MPI_FHIR_SYSTEM``,
default ``urn:openhis:mpi:mrn``). Each ``cross_references.system`` value is
exposed as ``urn:openhis:<system>`` (e.g. ``urn:openhis:openmrs``).

Auth: every route is gated by ``require_token`` (router-level dependency),
mirroring routers/patients.py; JWTMiddleware in main.py applies globally.

Route-ordering note: if a ``GET /fhir/Patient/{pid}`` read endpoint is ever
added, it must be declared AFTER the ``$ihe-pix`` route or FastAPI will
swallow ``$ihe-pix`` as a path parameter.
"""
import os
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from database import get_db, rows_to_list
from openhis_sdk.auth import require_token

router = APIRouter(prefix="/fhir", tags=["fhir"], dependencies=[Depends(require_token)])

#: Identifier system under which the MPI publishes its master MRN.
MPI_SYSTEM = os.environ.get("MPI_FHIR_SYSTEM", "urn:openhis:mpi:mrn")

#: cross_references.system values are exposed as ``urn:openhis:<system>``.
_XREF_PREFIX = "urn:openhis:"

_GENDER_MAP = {"m": "male", "f": "female"}


# ── helpers ───────────────────────────────────────────────────────────────────


def _token(s: Optional[str]) -> tuple[Optional[str], str]:
    """Parse a FHIR token ``system|value``.

    A bare value (no ``|``) or an empty system part means system=None
    (match any). Raises HTTPException(400) on a missing or empty value.
    """
    if not s:
        raise HTTPException(400, "identifier token must not be empty")
    if "|" in s:
        system, value = s.split("|", 1)
        system = system or None
    else:
        system, value = None, s
    if not value:
        raise HTTPException(400, f"identifier token {s!r} has an empty value part")
    return system, value


def _operation_outcome(code: str, diagnostics: str) -> dict[str, Any]:
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": code, "diagnostics": diagnostics}],
    }


def _outcome_response(status: int, code: str, diagnostics: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content=_operation_outcome(code, diagnostics)
    )


def _full_system(system: str) -> str:
    """Normalise a token system to the form used in Patient.identifier."""
    if system == MPI_SYSTEM or system.startswith(_XREF_PREFIX):
        return system
    return f"{_XREF_PREFIX}{system}"


def _identifiers(db: Any, row: Mapping[str, Any]) -> list[dict[str, str]]:
    """All identifiers for a master record: MRN first, then cross-references."""
    xrefs = rows_to_list(db.execute(
        "SELECT system, system_id FROM cross_references WHERE master_id=?",
        (row["id"],),
    ).fetchall())
    idents = [{"system": MPI_SYSTEM, "value": row["mrn"]}]
    idents += [
        {"system": f"{_XREF_PREFIX}{x['system']}", "value": x["system_id"]}
        for x in xrefs
    ]
    return idents


def _to_fhir_patient(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a master_patients row (+ its cross-references) to a FHIR Patient."""
    patient: dict[str, Any] = {
        "resourceType": "Patient",
        "id": row["id"],
        "active": row["status"] == "active",
        "identifier": _identifiers(db, row),
        "name": [{"family": row["lastname"], "given": [row["firstname"]]}],
    }
    sex = row.get("sex")
    if sex:
        patient["gender"] = _GENDER_MAP.get(sex.lower(), "unknown")
    if row.get("birthdate"):
        patient["birthDate"] = row["birthdate"]
    if row.get("phone"):
        patient["telecom"] = [{"system": "phone", "value": row["phone"]}]
    if row.get("address"):
        patient["address"] = [{"text": row["address"]}]
    return patient


def _resolve_master(
    db: Any, system: Optional[str], value: str
) -> Optional[dict[str, Any]]:
    """Resolve an identifier token to a master_patients row, or None.

    system None or MPI_SYSTEM → active master MRN lookup; anything else →
    cross_references lookup (``urn:openhis:`` prefix stripped if present).
    """
    if system in (None, MPI_SYSTEM):
        row = db.execute(
            "SELECT * FROM master_patients WHERE mrn=? AND status='active'",
            (value,),
        ).fetchone()
        return dict(row) if row else None
    xref = db.execute(
        "SELECT master_id FROM cross_references WHERE system=? AND system_id=?",
        (system.removeprefix(_XREF_PREFIX), value),
    ).fetchone()
    if not xref:
        return None
    row = db.execute(
        "SELECT * FROM master_patients WHERE id=?", (xref["master_id"],)
    ).fetchone()
    return dict(row) if row else None


def _entry(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    return {"fullUrl": f"Patient/{row['id']}", "resource": _to_fhir_patient(db, row)}


def _bundle(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }


# ── GET /fhir/Patient/$ihe-pix ────────────────────────────────────────────────
# Declared before any potential /Patient/{pid} read route (see module docstring).


@router.get("/Patient/$ihe-pix")
def ihe_pix(
    source_identifier: Optional[str] = Query(None, alias="sourceIdentifier"),
    target_system: Optional[str] = Query(None, alias="targetSystem"),
) -> Any:
    """IHE PIXm-style cross-reference query (pragmatic subset)."""
    if source_identifier is None:
        return _outcome_response(
            400, "required", "sourceIdentifier query parameter is required"
        )
    try:
        system, value = _token(source_identifier)
    except HTTPException as exc:
        return _outcome_response(400, "invalid", str(exc.detail))
    if system is None:
        return _outcome_response(
            400, "invalid",
            "sourceIdentifier must be a 'system|value' token with a system part",
        )

    with get_db() as db:
        row = _resolve_master(db, system, value)
        if not row:
            return _outcome_response(
                404, "not-found",
                f"No patient cross-referenced to sourceIdentifier {source_identifier!r}",
            )
        idents = _identifiers(db, row)

    queried = (_full_system(system), value)
    targets = [i for i in idents if (i["system"], i["value"]) != queried]
    if target_system:
        targets = [i for i in targets if i["system"] == target_system]

    parameter: list[dict[str, Any]] = [
        {
            "name": "targetIdentifier",
            "valueIdentifier": {"system": i["system"], "value": i["value"]},
        }
        for i in targets
    ]
    parameter.append(
        {"name": "targetId", "valueReference": {"reference": f"Patient/{row['id']}"}}
    )
    return {"resourceType": "Parameters", "parameter": parameter}


# ── GET /fhir/Patient ─────────────────────────────────────────────────────────


@router.get("/Patient")
def search_patient(
    identifier: Optional[str] = None,
    family: Optional[str] = None,
    given: Optional[str] = None,
    birthdate: Optional[str] = None,
) -> Any:
    """FHIR Patient search — by identifier token or basic demographics."""
    if identifier is not None:
        try:
            system, value = _token(identifier)
        except HTTPException as exc:
            return _outcome_response(400, "invalid", str(exc.detail))
        with get_db() as db:
            row = _resolve_master(db, system, value)
            entries = [_entry(db, row)] if row else []
        return _bundle(entries)

    if family and given and birthdate:
        with get_db() as db:
            rows = rows_to_list(db.execute(
                "SELECT * FROM master_patients WHERE firstname LIKE ? "
                "AND lastname LIKE ? AND birthdate=? AND status='active'",
                (f"%{given}%", f"%{family}%", birthdate),
            ).fetchall())
            entries = [_entry(db, r) for r in rows]
        return _bundle(entries)

    return _outcome_response(
        400, "invalid",
        "Provide an identifier token or family+given+birthdate search parameters",
    )
