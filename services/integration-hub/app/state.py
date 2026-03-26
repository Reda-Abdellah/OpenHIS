"""Mutable process-level counters shared between worker and API."""

patients_synced: int = 0
orders_synced:   int = 0
reports_synced:  int = 0
errors:          int = 0
last_poll_at:    str = ""
