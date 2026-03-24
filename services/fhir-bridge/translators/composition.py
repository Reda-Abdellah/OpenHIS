"""FHIR R4 Composition — maps clinical notes from EHR."""

# LOINC code map: note_type → (code, display)
_LOINC = {
    "progress":     ("11506-3", "Progress note"),
    "soap":         ("11506-3", "Progress note"),
    "nursing":      ("46240-8", "History and physical note"),
    "discharge":    ("18842-5", "Discharge summary"),
    "consultation": ("11488-4", "Consult note"),
    "procedure":    ("28570-0", "Procedure note"),
}

_STATUS_MAP = {
    "final":   "final",
    "draft":   "preliminary",
    "amended": "amended",
}


def to_fhir_composition(note: dict) -> dict:
    note_type    = note.get("note_type", "progress")
    loinc_code, loinc_display = _LOINC.get(note_type, ("11506-3", "Progress note"))
    fhir_status  = _STATUS_MAP.get(note.get("status", "draft"), "preliminary")
    content_div  = (note.get("content") or "").replace("<", "&lt;").replace(">", "&gt;")
    return {
        "resourceType": "Composition",
        "id":           f"note-{note.get('id', 'unknown')}",
        "status":       fhir_status,
        "type": {
            "coding": [{
                "system":  "http://loinc.org",
                "code":    loinc_code,
                "display": loinc_display,
            }],
            "text": note_type.title()
        },
        "subject":  {"reference": f"Patient/{note.get('patient_id', 'unknown')}"},
        "date":     note.get("signed_at") or note.get("createdat", ""),
        "author":   [{"display": note.get("author") or "Unknown"}],
        "title":    note.get("title") or loinc_display,
        "section":  [{
            "title": note.get("title") or loinc_display,
            "text": {
                "status": "generated",
                "div": (
                    f'<div xmlns="http://www.w3.org/1999/xhtml">'
                    f"<pre>{content_div}</pre></div>"
                )
            }
        }],
        "encounter": {"reference": f"Encounter/{note['encounter_id']}"}
        if note.get("encounter_id") else None,
    }
