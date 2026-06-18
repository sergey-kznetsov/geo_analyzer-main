from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv


CATALOG_URL = "https://catalog.api.2gis.com"


def _load_api_key() -> str:
    """Загружает API-ключ 2GIS из .env."""
    load_dotenv()

    for name in ["DGIS_API_KEY", "DGIS_KEY", "TWO_GIS_API_KEY"]:
        value = os.getenv(name)
        if value:
            return value

    raise RuntimeError(
        "Не найден ключ 2GIS. Добавь в .env переменную DGIS_API_KEY."
    )


def _request_items(
    query: str,
    city: str,
    api_key: str,
    page_size: int = 5,
) -> dict[str, Any]:
    """Делает тестовый запрос в 2GIS по тексту, чтобы вытащить рубрики из ответа."""
    params = {
        "q": query,
        "location": city,
        "key": api_key,
        "page_size": page_size,
        "fields": "items.rubrics,items.category_groups,items.name,items.full_name,items.type,items.type_id",
    }

    response = requests.get(
        f"{CATALOG_URL}/3.0/items",
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    return response.json()


def _extract_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Достаёт items из ответа 2GIS."""
    result = data.get("result")

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return result["items"]

    if isinstance(data.get("items"), list):
        return data["items"]

    return []


def _extract_rubrics(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Достаёт рубрики из объекта."""
    rubrics = item.get("rubrics")

    if isinstance(rubrics, list):
        return [rubric for rubric in rubrics if isinstance(rubric, dict)]

    return []


def discover_rubrics(
    config_path: Path,
    output_path: Path,
    city: str,
) -> None:
    """Собирает кандидаты rubric_id по категориям из config.yaml."""
    api_key = _load_api_key()

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    entries = config.get("dgis", {}).get("place_queries", [])

    if not entries:
        raise RuntimeError("В config.yaml не найден блок dgis.place_queries.")

    rows: list[dict[str, Any]] = []

    for entry in entries:
        category = str(entry.get("category", "")).strip()

        if not category:
            continue

        print(f"[2GIS] Ищу рубрики для: {category}")

        try:
            data = _request_items(
                query=category,
                city=city,
                api_key=api_key,
                page_size=10,
            )
        except requests.HTTPError as exc:
            rows.append(
                {
                    "category": category,
                    "status": "http_error",
                    "error": str(exc),
                    "rubric_id": "",
                    "rubric_name": "",
                    "sample_org": "",
                }
            )
            continue

        items = _extract_items(data)

        if not items:
            rows.append(
                {
                    "category": category,
                    "status": "not_found",
                    "error": "",
                    "rubric_id": "",
                    "rubric_name": "",
                    "sample_org": "",
                }
            )
            continue

        seen: set[str] = set()

        for item in items:
            sample_org = item.get("name") or item.get("full_name") or ""

            for rubric in _extract_rubrics(item):
                rubric_id = str(rubric.get("id") or "").strip()
                rubric_name = str(rubric.get("name") or rubric.get("alias") or "").strip()

                if not rubric_id:
                    continue

                key = f"{category}|{rubric_id}"

                if key in seen:
                    continue

                seen.add(key)

                rows.append(
                    {
                        "category": category,
                        "status": "candidate",
                        "error": "",
                        "rubric_id": rubric_id,
                        "rubric_name": rubric_name,
                        "sample_org": sample_org,
                    }
                )

        if not seen:
            rows.append(
                {
                    "category": category,
                    "status": "no_rubrics_in_response",
                    "error": "",
                    "rubric_id": "",
                    "rubric_name": "",
                    "sample_org": items[0].get("name") or items[0].get("full_name") or "",
                }
            )

    import pandas as pd

    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)

    print(f"[OK] Кандидаты rubric_id сохранены: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Путь до config.yaml",
    )
    parser.add_argument(
        "--output",
        default="output/2gis_rubric_candidates.xlsx",
        help="Куда сохранить Excel с кандидатами rubric_id",
    )
    parser.add_argument(
        "--city",
        default="Ижевск",
        help="Город для поиска рубрик",
    )

    args = parser.parse_args()

    discover_rubrics(
        config_path=Path(args.config),
        output_path=Path(args.output),
        city=args.city,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())