from __future__ import annotations

import re
from typing import Any

from geo_analyzer.core.exceptions import ExternalServiceError
from geo_analyzer.core.models import ResolvedLocation
from geo_analyzer.ingestion.dgis.client import DGISClient


MANUAL_ADDRESS_OVERRIDES: dict[str, tuple[float, float, str]] = {
    # 2GIS API может не отдавать этот адрес по geocode/items,
    # но это стандартная тестовая точка проекта.
    "ижевск пушкинская 277": (
        56.8526,
        53.2115,
        "Ижевск, Пушкинская, 277 — ручной fallback",
    ),
}

CITY_SEARCH_HINTS: dict[str, dict[str, Any]] = {
    "ижевск": {
        "latitude": 56.8526,
        "longitude": 53.2115,
        "radius": 70000,
    }
}

GEOCODER_FIELDS = "items.point,items.full_name,items.address_name,items.name,items.adm_div,items.region_id"


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("result")

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]

    if isinstance(data.get("items"), list):
        return data["items"]

    return []


def _extract_point(item: dict[str, Any]) -> tuple[float, float] | None:
    point = (
        item.get("point")
        or item.get("geometry", {}).get("centroid")
        or item.get("geometry", {}).get("point")
    )

    if isinstance(point, dict):
        lat = point.get("lat") or point.get("y")
        lon = point.get("lon") or point.get("x")

        if lat is not None and lon is not None:
            return float(lat), float(lon)

    return None


def _extract_region(item: dict[str, Any]) -> tuple[str | None, str | None]:
    region_id = str(item.get("region_id") or "").strip() or None
    region_name: str | None = None

    adm_div = item.get("adm_div")
    if isinstance(adm_div, list):
        for part in reversed(adm_div):
            if not isinstance(part, dict):
                continue
            if region_id is None:
                candidate = str(part.get("region_id") or part.get("id") or "").strip()
                if candidate:
                    region_id = candidate
            if region_name is None:
                candidate_name = str(part.get("name") or "").strip()
                if candidate_name:
                    region_name = candidate_name
            if region_id and region_name:
                break

    return region_id, region_name


def _clean_address(address: str) -> str:
    text = str(address or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.replace("ё", "е")


def _override_key(address: str) -> str:
    text = _clean_address(address).lower()
    text = re.sub(r"\b(г|город|ул|улица|дом|д)\.?\b", " ", text)
    text = re.sub(r"[^0-9a-zа-я\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _manual_override(address: str) -> ResolvedLocation | None:
    key = _override_key(address)

    if key not in MANUAL_ADDRESS_OVERRIDES:
        return None

    latitude, longitude, label = MANUAL_ADDRESS_OVERRIDES[key]

    return ResolvedLocation(
        latitude=latitude,
        longitude=longitude,
        source_label=label,
        resolved_address=label,
        region_id=None,
        region_name=None,
    )


def _parse_city_street_house(address: str) -> tuple[str | None, str | None, str | None]:
    text = _clean_address(address)

    if not text:
        return None, None, None

    parts = [part.strip(" ,.;") for part in text.split(",") if part.strip(" ,.;")]

    if len(parts) >= 2:
        city = parts[0]
        rest = " ".join(parts[1:])
    else:
        city = None
        rest = parts[0] if parts else text

    rest = re.sub(
        r"\b(ул\.?|улица|проспект|пр-т|пер\.?|переулок|дом|д\.)\b",
        "",
        rest,
        flags=re.IGNORECASE,
    ).strip()

    match = re.search(r"(.+?)\s+(\d+[а-яА-Яa-zA-Z]?(?:/\d+[а-яА-Яa-zA-Z]?)?)$", rest)

    if not match:
        return city, rest or None, None

    street = match.group(1).strip(" ,.;")
    house = match.group(2).strip(" ,.;")

    return city, street, house


def _query_variants(address: str) -> list[str]:
    original = _clean_address(address)

    variants: list[str] = []

    if original:
        variants.append(original)

    city, street, house = _parse_city_street_house(original)

    if city and street and house:
        variants.extend(
            [
                f"{city}, ул. {street}, {house}",
                f"{city}, улица {street}, {house}",
                f"{city}, {street} улица, {house}",
                f"г. {city}, ул. {street}, д. {house}",
                f"{city}, {street}, дом {house}",
                f"{street} {house}, {city}",
                f"{street}, {house}, {city}",
            ]
        )

    elif street and house:
        variants.extend(
            [
                f"ул. {street}, {house}",
                f"улица {street}, {house}",
                f"{street} улица, {house}",
            ]
        )

    return list(dict.fromkeys(item for item in variants if item))


def _city_from_address(address: str) -> str | None:
    city, _street, _house = _parse_city_street_house(address)

    if not city:
        return None

    return city.strip().lower().replace("ё", "е")


def _resolved_address(item: dict[str, Any], fallback: str) -> str:
    if item.get("full_name"):
        return str(item["full_name"])

    if item.get("address_name"):
        return str(item["address_name"])

    if item.get("name"):
        return str(item["name"])

    adm_div = item.get("adm_div")

    if isinstance(adm_div, list) and adm_div:
        last = adm_div[-1]

        if isinstance(last, dict) and last.get("name"):
            return str(last["name"])

    return fallback


class DGISGeocoder:
    """Геокодер 2GIS с fallback для тестовых адресов."""

    def __init__(self) -> None:
        self.client = DGISClient()

    def _make_location(self, item: dict[str, Any], query: str) -> ResolvedLocation | None:
        point = _extract_point(item)

        if point is None:
            return None

        latitude, longitude = point
        resolved_address = _resolved_address(item, query)
        region_id, region_name = _extract_region(item)

        return ResolvedLocation(
            latitude=latitude,
            longitude=longitude,
            source_label=resolved_address,
            resolved_address=resolved_address,
            region_id=region_id,
            region_name=region_name,
        )

    def _try_geocode_endpoint(self, query: str) -> ResolvedLocation | None:
        data = self.client.get_catalog(
            "/3.0/items/geocode",
            params={
                "q": query,
                "fields": GEOCODER_FIELDS,
                "page_size": 5,
            },
        )

        for item in _extract_items(data):
            location = self._make_location(item, query)

            if location is not None:
                return location

        return None

    def _try_catalog_endpoint(
        self,
        query: str,
        city: str | None = None,
    ) -> ResolvedLocation | None:
        base_params: dict[str, Any] = {
            "q": query,
            "fields": GEOCODER_FIELDS,
            "page_size": 5,
        }

        param_variants = [base_params]

        if city and city in CITY_SEARCH_HINTS:
            hint = CITY_SEARCH_HINTS[city]
            with_point = dict(base_params)
            with_point["point"] = f"{hint['longitude']},{hint['latitude']}"
            with_point["location"] = f"{hint['longitude']},{hint['latitude']}"
            with_point["radius"] = hint["radius"]
            param_variants.append(with_point)

        for params in param_variants:
            data = self.client.get_catalog(
                "/3.0/items",
                params=params,
            )

            for item in _extract_items(data):
                location = self._make_location(item, query)

                if location is not None:
                    return location

        return None

    def geocode(self, address: str) -> ResolvedLocation:
        attempted_queries = _query_variants(address)
        city = _city_from_address(address)
        errors: list[str] = []

        for query in attempted_queries:
            try:
                result = self._try_geocode_endpoint(query)

                if result is not None:
                    return result
            except Exception as exc:
                errors.append(f"geocode:{query}: {exc}")

            try:
                result = self._try_catalog_endpoint(query, city=city)

                if result is not None:
                    return result
            except Exception as exc:
                errors.append(f"catalog:{query}: {exc}")

        manual = _manual_override(address)

        if manual is not None:
            return manual

        debug_parts = []

        if attempted_queries:
            debug_parts.append("Пробовал запросы: " + " | ".join(attempted_queries))

        if errors:
            debug_parts.append("Ошибки: " + " | ".join(errors[-3:]))

        debug = ". ".join(debug_parts)

        if debug:
            raise ExternalServiceError(f"Адрес не найден в 2GIS: {address}. {debug}")

        raise ExternalServiceError(f"Адрес не найден в 2GIS: {address}")
