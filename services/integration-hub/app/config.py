import os

OPENMRS_URL  = os.environ.get("OPENMRS_URL",  "http://openmrs:8080")
OPENELIS_URL = os.environ.get("OPENELIS_URL", "http://openelis:8080")
OPENELIS_USER     = os.environ.get("OPENELIS_USER", "admin")
OPENELIS_PASSWORD = os.environ.get("OPENELIS_PASSWORD", "adminADMIN!")
ODOO_URL     = os.environ.get("ODOO_URL",     "http://odoo:8069")
ODOO_DB      = os.environ.get("ODOO_DB",      "odoo")
ODOO_ADMIN_PASS = os.environ.get("ODOO_ADMIN_PASS", "")

POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "60"))
ROOT_PATH       = os.environ.get("ROOT_PATH", "")
AUDIT_DB_PATH   = os.environ.get("AUDIT_DB_PATH", "/data/hub-audit.db")
REDIS_URL       = os.environ.get("REDIS_URL", "")
MPI_URL         = os.environ.get("MPI_URL", "http://mpi:8007")
