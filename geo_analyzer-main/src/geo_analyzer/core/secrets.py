from __future__ import annotations

import os


def _read_embedded_key() -> str:
    try:
        from geo_analyzer.core._embedded_secret import get_embedded_dgis_api_key

        return str(get_embedded_dgis_api_key() or "").strip()
    except Exception:
        return ""


def get_dgis_api_key() -> str:
    """Return 2GIS API key from environment or embedded build secret.

    Priority:
    1. DGIS_API_KEY from .env/environment during development.
    2. Alternative common environment names.
    3. Embedded key generated during Windows build.

    No demo fallback is used here: if key is missing, API calls should fail
    explicitly instead of silently spending demo quota.
    """

    for name in ("DGIS_API_KEY", "DGIS_KEY", "TWOGIS_API_KEY", "API_KEY_2GIS", "2GIS_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value

    return _read_embedded_key()


DGIS_API_KEY = get_dgis_api_key()
