# Re-exports from the shared SDK — do not add logic here.
# To update auth logic, modify libs/openhis_sdk/src/openhis_sdk/auth.py
from openhis_sdk.auth import JWTMiddleware  # noqa: F401
