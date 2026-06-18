from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


APP_NAME = "GeoAnalyzer"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resource_root() -> Path:
    """Return the PyInstaller resource root or project root in dev mode."""
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)

        internal = _exe_dir() / "_internal"
        if internal.exists():
            return internal

        return _exe_dir()

    return _project_root()


def _app_data_dir() -> Path:
    """Return the writable runtime root.

    Portable Windows builds write next to the executable. Source runs write into
    the repository ``data`` directory.
    """
    if _is_frozen():
        return _exe_dir()
    return _project_root() / "data"


def _load_env_for_dev() -> None:
    if _is_frozen():
        return

    env_path = _project_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _read_embedded_dgis_key() -> str:
    try:
        from geo_analyzer.core._embedded_secret import get_embedded_dgis_api_key
        return str(get_embedded_dgis_api_key() or "").strip()
    except Exception:
        return ""


def _read_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    return data if isinstance(data, dict) else {}


def _first_existing_config_path() -> Path | None:
    resource_root = _resource_root()
    exe_dir = _exe_dir()
    project_root = _project_root()

    candidates = [
        resource_root / "config" / "config.yaml",
        resource_root / "config.yaml",
        exe_dir / "_internal" / "config" / "config.yaml",
        exe_dir / "config" / "config.yaml",
        exe_dir / "config.yaml",
        project_root / "config" / "config.yaml",
        project_root / "config.yaml",
    ]

    return next((candidate for candidate in candidates if candidate.exists()), None)


def _default_place_queries() -> list[dict[str, str]]:
    """Minimal fallback if bundled config.yaml is missing."""
    return [
        {"category": "Супермаркеты", "rubric_name": "Супермаркеты", "rubric_id": "350"},
        {"category": "Продуктовые магазины", "rubric_name": "Продуктовые магазины", "rubric_id": "515"},
        {"category": "Аптеки", "rubric_name": "Аптеки", "rubric_id": "273"},
        {"category": "Кафе", "rubric_name": "Кафе", "rubric_id": "164"},
        {"category": "Рестораны", "rubric_name": "Рестораны", "rubric_id": "161"},
        {"category": "Детские сады", "rubric_name": "Детские сады", "rubric_id": "196"},
        {"category": "Школы", "rubric_name": "Школы", "rubric_id": "194"},
        {"category": "Фитнес-клубы", "rubric_name": "Фитнес-клубы", "rubric_id": "453"},
        {"category": "Парикмахерские", "rubric_name": "Парикмахерские", "rubric_id": "408"},
        {"category": "Пункты выдачи интернет-заказов", "rubric_name": "Пункты выдачи интернет-заказов", "rubric_id": "70000001007257537"},
        {"category": "Остановки общественного транспорта", "rubric_name": "Остановки общественного транспорта", "rubric_id": "450"},
    ]


def _default_config() -> dict[str, Any]:
    return {
        "dgis": {
            "catalog_url": "https://catalog.api.2gis.com",
            "routing_url": "https://routing.api.2gis.com",
            "timeout": 30,
            "region_id": "32",
            "places_page_size": 10,
            "places_max_pages": 2,
            "place_queries": _default_place_queries(),
        },
        "analysis": {
            "poi_radius_m": 1200,
            "graph_dist_m": 1200,
            "isochrone_minutes": [5, 10, 15],
            "walk_speed_kph": 4.8,
        },
        "city_centers": {
            "ижевск": {
                "city": "Ижевск",
                "name": "Центр Ижевска / Центральная площадь",
                "latitude": 56.8526,
                "longitude": 53.2115,
                "source": "config_city_centers",
            }
        },
        "city_center_search_queries": [
            "{city}, Центральная площадь",
            "{city}, центр",
            "центр {city}",
            "{city}",
        ],
        "poi_rubric_ids": [],
        "benchmark": {"district": {}, "city": {}},
        "weights": {},
        "poi_classification": {},
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "да"}


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class Settings:
    config: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None

    def __post_init__(self) -> None:
        _load_env_for_dev()
        self.config_path = _first_existing_config_path()
        self.config = _deep_merge(_default_config(), _read_yaml(self.config_path))

        _ensure_dir(self.app_data_dir)
        _ensure_dir(self.data_dir)
        _ensure_dir(self.output_dir)
        _ensure_dir(self.cache_dir)
        _ensure_dir(self.benchmark_dir)
        _ensure_dir(self.logs_dir)

    @property
    def is_frozen(self) -> bool:
        return _is_frozen()

    @property
    def app_dir(self) -> Path:
        return _exe_dir()

    @property
    def resource_root(self) -> Path:
        return _resource_root()

    @property
    def project_root(self) -> Path:
        return _project_root()

    @property
    def app_data_dir(self) -> Path:
        return _ensure_dir(_app_data_dir())

    @property
    def data_dir(self) -> Path:
        return _ensure_dir(self.app_data_dir)

    @property
    def output_dir(self) -> Path:
        return _ensure_dir(self.data_dir / "output")

    @property
    def cache_dir(self) -> Path:
        return _ensure_dir(self.data_dir / "cache")

    @property
    def benchmark_dir(self) -> Path:
        return _ensure_dir(self.data_dir / "benchmarks")

    @property
    def benchmarks_dir(self) -> Path:
        return self.benchmark_dir

    @property
    def logs_dir(self) -> Path:
        return _ensure_dir(self.data_dir / "logs")

    @property
    def dgis_api_key(self) -> str:
        env_key = os.getenv("DGIS_API_KEY", "").strip()
        if env_key:
            return env_key

        portable_env = self.app_dir / ".env"
        if portable_env.exists():
            try:
                for line in portable_env.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "DGIS_API_KEY":
                        parsed = value.strip().strip('"').strip("'")
                        if parsed:
                            return parsed
            except Exception:
                pass

        return _read_embedded_dgis_key()

    @property
    def dgis_catalog_url(self) -> str:
        return str(self.config.get("dgis", {}).get("catalog_url", "https://catalog.api.2gis.com")).rstrip("/")

    @property
    def dgis_routing_url(self) -> str:
        return str(self.config.get("dgis", {}).get("routing_url", "https://routing.api.2gis.com")).rstrip("/")

    @property
    def dgis_timeout(self) -> int:
        return _as_int(self.config.get("dgis", {}).get("timeout"), 30)

    @property
    def dgis_region_id(self) -> str:
        return str(self.config.get("dgis", {}).get("region_id", "")).strip()

    @property
    def dgis_places_page_size(self) -> int:
        raw = self.config.get("dgis", {}).get("places_page_size") or self.config.get("dgis", {}).get("page_size") or 10
        return max(1, min(_as_int(raw, 10), 10))

    @property
    def dgis_places_max_pages(self) -> int:
        raw = self.config.get("dgis", {}).get("places_max_pages") or self.config.get("dgis", {}).get("max_pages") or 2
        return max(1, _as_int(raw, 2))

    @property
    def dgis_place_queries(self) -> list[dict[str, str]]:
        raw = (
            self.config.get("dgis", {}).get("place_queries")
            or self.config.get("dgis_place_queries")
            or self.config.get("poi", {}).get("place_queries")
            or []
        )
        if not isinstance(raw, list):
            return []

        result: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue

            category = str(item.get("category") or item.get("name") or item.get("Категория") or "").strip()
            rubric_id = str(item.get("rubric_id") or item.get("id") or item.get("rubric") or "").strip()
            rubric_name = str(item.get("rubric_name") or item.get("rubric") or item.get("name") or category or "").strip()

            if not category:
                continue

            result.append({"category": category, "rubric_name": rubric_name, "rubric_id": rubric_id})

        return result

    @property
    def no_api(self) -> bool:
        return _env_bool("GEO_ANALYZER_NO_API", False)

    @property
    def use_cache(self) -> bool:
        return not _env_bool("GEO_ANALYZER_DISABLE_CACHE", False)

    @property
    def refresh_cache(self) -> bool:
        return _env_bool("GEO_ANALYZER_REFRESH_CACHE", False)

    @property
    def refresh_city_benchmark(self) -> bool:
        return _env_bool("GEO_ANALYZER_REFRESH_CITY_BENCHMARK", False)

    @property
    def poi_radius_m(self) -> int:
        return _as_int(self.config.get("analysis", {}).get("poi_radius_m"), 1200)

    @property
    def graph_dist_m(self) -> int:
        return _as_int(self.config.get("analysis", {}).get("graph_dist_m"), 1200)

    @property
    def isochrone_minutes(self) -> list[int]:
        raw = self.config.get("analysis", {}).get("isochrone_minutes", [5, 10, 15])
        if not isinstance(raw, list):
            return [5, 10, 15]

        result: list[int] = []
        for item in raw:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue

        return result or [5, 10, 15]

    @property
    def walk_speed_kph(self) -> float:
        return _as_float(self.config.get("analysis", {}).get("walk_speed_kph"), 4.8)

    @property
    def poi_rubric_ids(self) -> list[str]:
        raw = self.config.get("poi_rubric_ids", [])
        return [str(item) for item in raw] if isinstance(raw, list) else []

    @property
    def city_centers(self) -> dict[str, Any]:
        raw = self.config.get("city_centers", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def city_center_search_queries(self) -> list[str]:
        raw = self.config.get("city_center_search_queries", [])
        return [str(item) for item in raw] if isinstance(raw, list) else []

    @property
    def benchmark_district(self) -> dict[str, Any]:
        raw = self.config.get("benchmark", {}).get("district", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def benchmark_city(self) -> dict[str, Any]:
        raw = self.config.get("benchmark", {}).get("city", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def weights(self) -> dict[str, Any]:
        raw = self.config.get("weights", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def poi_classification(self) -> dict[str, Any]:
        raw = self.config.get("poi_classification", {})
        return raw if isinstance(raw, dict) else {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
