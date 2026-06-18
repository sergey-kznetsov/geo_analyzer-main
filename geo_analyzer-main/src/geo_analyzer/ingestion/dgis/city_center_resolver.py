from __future__ import annotations

import re
from typing import Any

from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.models import ResolvedLocation
from geo_analyzer.core.settings import get_settings
from geo_analyzer.ingestion.dgis.geocoder import DGISGeocoder

logger = get_logger("geo_analyzer.dgis.city_center")


def _normalize_city_name(value: str | None) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = re.sub(r"\s+", " ", text)
    text = text.replace("город ", "").replace("г. ", "").strip(" ,.;")

    if not text:
        return None

    return text


def _is_coordinate_like(value: str | None) -> bool:
    if value is None:
        return False

    text = str(value).strip()

    if not text:
        return False

    if re.fullmatch(r"[-+]?\d+([.,]\d+)?", text):
        return True

    if re.fullmatch(
        r"\s*[-+]?\d+([.,]\d+)?\s*,\s*[-+]?\d+([.,]\d+)?\s*",
        text,
    ):
        return True

    return False


def _is_valid_city_name(value: str | None) -> bool:
    if value is None:
        return False

    text = str(value).strip()

    if not text:
        return False

    lowered = text.lower()

    if lowered in {"coordinates", "unknown", "unknown_city", "none", "null"}:
        return False

    if _is_coordinate_like(text):
        return False

    # Название города не должно состоять только из цифр, точек и запятых.
    if not re.search(r"[a-zA-Zа-яА-Я]", text):
        return False

    return True


def _extract_city_from_address(address: str | None) -> str | None:
    """Пытается достать город из адресной строки.

    Поддерживает форматы:
    - "Ижевск, Пушкинская, 277"
    - "г. Ижевск, ул. Пушкинская, 277"
    - "Россия, Удмуртская Республика, Ижевск, Пушкинская, 277"

    Если строка похожа на координаты, город не возвращается.
    """
    if not address:
        return None

    text = str(address).strip()

    if _is_coordinate_like(text):
        return None

    parts = [part.strip() for part in text.split(",") if part.strip()]

    if not parts:
        return None

    stop_words = {
        "россия",
        "российская федерация",
        "удмуртская республика",
        "республика удмуртия",
        "пермский край",
        "московская область",
        "ленинградская область",
        "свердловская область",
        "нижегородская область",
    }

    street_markers = {
        "ул.",
        "улица",
        "проспект",
        "пр-т",
        "пер.",
        "переулок",
        "шоссе",
        "площадь",
        "наб.",
        "набережная",
        "бульвар",
    }

    candidates: list[str] = []

    for part in parts:
        normalized = part.strip()
        lowered = normalized.lower()

        if lowered in stop_words:
            continue

        if any(marker in lowered for marker in street_markers):
            continue

        if re.search(r"\d", lowered):
            continue

        candidate = _normalize_city_name(normalized)

        if _is_valid_city_name(candidate):
            candidates.append(candidate)

    if candidates:
        # Обычно город ближе к началу адреса, если передан обычный пользовательский адрес.
        return candidates[0]

    first = _normalize_city_name(parts[0])
    if _is_valid_city_name(first):
        return first

    return None


def extract_city(location: ResolvedLocation) -> str | None:
    """Достаёт город из ResolvedLocation.

    Для координат без reverse geocode город не придумываем. Это важно, чтобы
    не создавать benchmark-папки с именами вроде "56.8526".
    """
    source_label = str(getattr(location, "source_label", "") or "").lower()

    if source_label == "coordinates":
        return None

    resolved_address = getattr(location, "resolved_address", None)
    city = _extract_city_from_address(resolved_address)

    if _is_valid_city_name(city):
        return city

    return None


def _center_from_config(city: str) -> dict[str, Any] | None:
    settings = get_settings()
    centers = settings.city_centers

    if not centers:
        return None

    normalized_city = city.strip().lower().replace("ё", "е")

    for key, value in centers.items():
        normalized_key = str(key).strip().lower().replace("ё", "е")

        if normalized_key != normalized_city:
            continue

        if not isinstance(value, dict):
            continue

        lat = value.get("latitude")
        lon = value.get("longitude")

        if lat is None or lon is None:
            continue

        return {
            "city": city,
            "name": value.get("name") or f"Центр города {city}",
            "latitude": float(lat),
            "longitude": float(lon),
            "source": "config_city_centers",
        }

    return None


def _center_from_geocoder(city: str) -> dict[str, Any] | None:
    settings = get_settings()
    geocoder = DGISGeocoder()

    queries = settings.city_center_search_queries

    for template in queries:
        query = template.format(city=city)

        try:
            resolved = geocoder.geocode(query)
        except Exception as exc:
            logger.warning("Не удалось найти центр города по запросу '%s': %s", query, exc)
            continue

        latitude = getattr(resolved, "latitude", None)
        longitude = getattr(resolved, "longitude", None)

        if latitude is None or longitude is None:
            continue

        return {
            "city": city,
            "name": query,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "source": "2gis_geocoder_city_center",
        }

    return None


def resolve_city_center(location: ResolvedLocation) -> dict[str, Any] | None:
    """Определяет центр города для автомобильной доступности.

    Логика:
    1. достать город из resolved_address;
    2. если город есть в config.yaml → взять координаты оттуда;
    3. если города нет в config.yaml → попробовать найти центр через 2GIS Geocoder;
    4. если город не определён, вернуть None.

    Важно: для прямого запуска по координатам город не выдумывается.
    """
    city = extract_city(location)

    if not _is_valid_city_name(city):
        logger.warning(
            "Город не определён из адреса '%s'. Авто-доступность до центра будет fallback.",
            getattr(location, "resolved_address", None),
        )
        return None

    from_config = _center_from_config(city)
    if from_config is not None:
        logger.info(
            "Центр города '%s' взят из config.yaml: %s, %s",
            city,
            from_config["latitude"],
            from_config["longitude"],
        )
        return from_config

    from_geocoder = _center_from_geocoder(city)
    if from_geocoder is not None:
        logger.info(
            "Центр города '%s' найден через 2GIS Geocoder: %s, %s",
            city,
            from_geocoder["latitude"],
            from_geocoder["longitude"],
        )
        return from_geocoder

    logger.warning(
        "Центр города '%s' не найден. Авто-доступность до центра будет fallback.",
        city,
    )

    return None