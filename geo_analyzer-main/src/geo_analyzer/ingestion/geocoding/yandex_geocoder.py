from __future__ import annotations

import requests

from geo_analyzer.core.exceptions import ExternalServiceError
from geo_analyzer.core.models import ResolvedLocation
from geo_analyzer.core.settings import get_settings
from .base import BaseGeocoder


class YandexGeocoder(BaseGeocoder):
    """Геокодер Яндекса для адреса и обратного геокодирования."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def geocode(self, address: str) -> ResolvedLocation:
        params = {
            "apikey": self.settings.yandex_geocoder_api_key,
            "geocode": address,
            "format": "json",
            "lang": self.settings.yandex_lang,
            "results": 1,
        }
        response = requests.get(self.settings.yandex_geocoder_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        members = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
        if not members:
            raise ExternalServiceError(f"Адрес не найден: {address}")
        geo_object = members[0]["GeoObject"]
        lon_str, lat_str = geo_object["Point"]["pos"].split()
        resolved = geo_object.get("metaDataProperty", {}).get("GeocoderMetaData", {}).get("text")
        return ResolvedLocation(
            latitude=float(lat_str),
            longitude=float(lon_str),
            source_label=resolved or address,
            resolved_address=resolved or address,
        )

    def reverse_geocode(self, latitude: float, longitude: float) -> str | None:
        params = {
            "apikey": self.settings.yandex_geocoder_api_key,
            "geocode": f"{longitude},{latitude}",
            "format": "json",
            "lang": self.settings.yandex_lang,
            "results": 1,
        }
        response = requests.get(self.settings.yandex_geocoder_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        members = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
        if not members:
            return None
        geo_object = members[0]["GeoObject"]
        return geo_object.get("metaDataProperty", {}).get("GeocoderMetaData", {}).get("text")
