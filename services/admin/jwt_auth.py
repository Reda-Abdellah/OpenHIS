# Re-exports from the shared SDK — do not add logic here.
# To update auth logic, modify libs/openhis_sdk/src/openhis_sdk/auth.py
from openhis_sdk.auth import (  # noqa: F401
    KEYCLOAK_REALM,
    KEYCLOAK_URL,
    require_roles,
    require_token,
)
