from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
CANDIDATES_PATH = PROJECT_ROOT / "tools" / "2gis_rubric_candidates.yaml"


CATEGORY_SEARCH_ALIASES: dict[str, list[str]] = {
    "Супермаркеты": ["Супермаркеты"],
    "Продуктовые магазины": ["Продуктовые магазины", "Продовольственные магазины"],
    "Аптеки": ["Аптеки"],
    "Пункты выдачи заказов": ["Пункты выдачи интернет-заказов"],
    "Кофейни": ["Кофейни"],
    "Пекарни": ["Пекарни"],
    "Кафе": ["Кафе"],
    "Рестораны": ["Рестораны"],
    "Школы": ["Школы"],
    "Детские сады": ["Детские сады"],
    "Детское дополнительное образование": [
        "Центры раннего развития детей",
        "Детские развивающие центры",
        "Детские / подростковые клубы",
        "Детские музыкальные школы",
        "Детские художественные школы",
        "Спортивные секции",
    ],
    "Взрослое дополнительное образование": [
        "Переподготовка и повышение квалификации",
        "Учебные центры",
    ],
    "Детские игровые центры": ["Детские игровые залы"],
    "Медицинские центры": ["Многопрофильные медицинские центры"],
    "Поликлиники": [
        "Поликлиники для взрослых",
        "Детские поликлиники",
        "Поликлиники",
    ],
    "Ветеринарные клиники": ["Ветеринарные клиники"],
    "Зоомагазины": ["Зоотовары"],
    "Фитнес-клубы": ["Фитнес-клубы"],
    "Салоны красоты и парикмахерские": ["Парикмахерские"],
    "Ногтевые студии": ["Ногтевые студии"],
    "Химчистки": ["Химчистки одежды и текстиля"],
    "Ремонт обуви": ["Ремонт обуви и кожгалантереи"],
    "Ателье": ["Швейные ателье"],
    "Ремонт техники": ["Ремонт и установка бытовой техники"],
    "Хозяйственные магазины": ["Хозтовары"],
    "Парки и скверы": ["Парки"],
    "Торговые центры": ["Торговые центры"],
    "Театры": ["Театры"],
    "Музеи": ["Музеи"],
    "Кинотеатры": ["Кинотеатры"],
    "Стадионы": ["Стадионы / Спортивные арены"],
}


BLOCKED_AUTOMATIC_RUBRICS: dict[str, set[str]] = {
    "Поликлиники": {
        "стоматологические поликлиники",
        "стоматологии",
        "стоматологические центры",
    },
    "Детское дополнительное образование": {
        "переподготовка и повышение квалификации",
        "автошколы",
        "бизнес-школы",
    },
}


def _normalize(value: Any) -> str:
    return str(value or "").replace("ё", "е").strip().lower()


def _load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise RuntimeError("config/config.yaml должен быть YAML-словарём.")

    return data


def _save_config(config: dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            config,
            file,
            allow_unicode=True,
            sort_keys=False,
            width=140,
        )


def _save_candidates(data: dict[str, Any]) -> None:
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CANDIDATES_PATH.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            data,
            file,
            allow_unicode=True,
            sort_keys=False,
            width=160,
        )


def _get_region_id(city: str, api_key: str, catalog_url: str, timeout: int) -> tuple[str, str]:
    url = f"{catalog_url.rstrip('/')}/2.0/region/search"

    params = {
        "q": city,
        "country_code_filter": "ru",
        "page_size": 10,
        "key": api_key,
    }

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    items = data.get("result", {}).get("items", [])

    if not items:
        raise RuntimeError(f"2GIS Regions API не вернул region_id для города: {city}")

    exact = [item for item in items if _normalize(item.get("name")) == _normalize(city)]
    selected = exact[0] if exact else items[0]

    region_id = str(selected.get("id", "")).strip()
    region_name = str(selected.get("name", city)).strip()

    if not region_id:
        raise RuntimeError(f"В ответе Regions API нет id для города: {city}")

    return region_id, region_name


def _search_rubrics(
    query: str,
    region_id: str,
    api_key: str,
    catalog_url: str,
    timeout: int,
) -> list[dict[str, Any]]:
    url = f"{catalog_url.rstrip('/')}/2.0/catalog/rubric/search"

    params = {
        "q": query,
        "region_id": region_id,
        "page_size": 50,
        "fields": "items.rubrics,items.rubrics.region_id",
        "key": api_key,
    }

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()

    data = response.json()
    items = data.get("result", {}).get("items", [])

    return items if isinstance(items, list) else []


def _flatten_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for item in items:
        rubric_id = str(item.get("id", "")).strip()
        rubric_name = str(item.get("name") or item.get("title") or item.get("caption") or "").strip()

        if rubric_id:
            result.append(
                {
                    "rubric_id": rubric_id,
                    "rubric_name": rubric_name,
                    "title": item.get("title"),
                    "caption": item.get("caption"),
                    "keyword": item.get("keyword"),
                    "parent_id": item.get("parent_id"),
                    "org_count": item.get("org_count"),
                    "branch_count": item.get("branch_count"),
                    "source": "item",
                }
            )

        children = item.get("rubrics") or []

        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue

                child_id = str(child.get("id", "")).strip()
                child_name = str(child.get("name") or child.get("title") or child.get("caption") or "").strip()

                if not child_id:
                    continue

                result.append(
                    {
                        "rubric_id": child_id,
                        "rubric_name": child_name,
                        "title": child.get("title"),
                        "caption": child.get("caption"),
                        "keyword": child.get("keyword"),
                        "parent_id": child.get("parent_id"),
                        "org_count": child.get("org_count"),
                        "branch_count": child.get("branch_count"),
                        "source": "child",
                    }
                )

    unique: dict[str, dict[str, Any]] = {}

    for item in result:
        unique[item["rubric_id"]] = item

    return list(unique.values())


def _candidate_text(item: dict[str, Any]) -> str:
    return " ".join(
        _normalize(item.get(key))
        for key in ["rubric_name", "title", "caption", "keyword"]
        if item.get(key)
    )


def _is_blocked(category: str, candidate: dict[str, Any]) -> bool:
    blocked = BLOCKED_AUTOMATIC_RUBRICS.get(category, set())
    text = _candidate_text(candidate)

    return any(blocked_item in text for blocked_item in blocked)


def _pick_best(category: str, aliases: list[str], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    clean_candidates = [
        candidate for candidate in candidates
        if not _is_blocked(category, candidate)
    ]

    if not clean_candidates:
        return None

    normalized_aliases = [_normalize(alias) for alias in aliases if _normalize(alias)]

    for alias in normalized_aliases:
        exact = [
            item for item in clean_candidates
            if _normalize(item.get("rubric_name")) == alias
            or _normalize(item.get("title")) == alias
            or _normalize(item.get("caption")) == alias
        ]

        if exact:
            return exact[0]

    for alias in normalized_aliases:
        contains = [
            item for item in clean_candidates
            if alias in _candidate_text(item)
        ]

        if contains:
            return contains[0]

    return clean_candidates[0]


def _replace_place_queries(config: dict[str, Any]) -> list[dict[str, Any]]:
    dgis = config.setdefault("dgis", {})

    new_queries = [
        {"category": category, "rubric_id": "", "rubric_name": ""}
        for category in CATEGORY_SEARCH_ALIASES.keys()
    ]

    dgis["place_queries"] = new_queries
    return new_queries


def build_rubrics(city: str, dry_run: bool) -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=True)

    api_key = os.getenv("DGIS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Не задан DGIS_API_KEY в .env.")

    config = _load_config()
    dgis = config.setdefault("dgis", {})

    catalog_url = str(dgis.get("catalog_url", "https://catalog.api.2gis.com")).rstrip("/")
    timeout = int(dgis.get("timeout", 30))

    region_id, region_name = _get_region_id(
        city=city,
        api_key=api_key,
        catalog_url=catalog_url,
        timeout=timeout,
    )

    dgis["region_id"] = region_id
    dgis["region_name"] = region_name

    place_queries = _replace_place_queries(config)

    report: dict[str, Any] = {
        "city": city,
        "region_id": region_id,
        "region_name": region_name,
        "source": "Official 2GIS Categories API /2.0/catalog/rubric/search",
        "categories": [],
    }

    found_count = 0
    failed_count = 0

    print(f"Город: {city}")
    print(f"2GIS region_id: {region_id} ({region_name})")
    print("Источник рубрик: официальный Categories API /2.0/catalog/rubric/search")
    print("")

    for entry in place_queries:
        category = str(entry.get("category", "")).strip()
        aliases = CATEGORY_SEARCH_ALIASES.get(category, [category])

        all_candidates: list[dict[str, Any]] = []

        for query in aliases:
            try:
                items = _search_rubrics(
                    query=query,
                    region_id=region_id,
                    api_key=api_key,
                    catalog_url=catalog_url,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                print(f"[FAIL] {category}: ошибка Categories API query='{query}': {exc}")
                continue

            candidates = _flatten_candidates(items)
            all_candidates.extend(candidates)

            if candidates:
                break

        unique: dict[str, dict[str, Any]] = {}

        for candidate in all_candidates:
            unique[candidate["rubric_id"]] = candidate

        candidates = list(unique.values())
        best = _pick_best(category, aliases, candidates)

        if not best:
            print(f"[FAIL] {category}: точная рубрика не найдена")
            failed_count += 1

            report["categories"].append(
                {
                    "category": category,
                    "status": "not_found",
                    "aliases": aliases,
                    "candidates": candidates,
                }
            )
            continue

        rubric_id = str(best.get("rubric_id", "")).strip()
        rubric_name = str(best.get("rubric_name", "")).strip()

        entry["rubric_id"] = rubric_id
        entry["rubric_name"] = rubric_name

        print(f"[OK] {category}: rubric_id={rubric_id}, rubric_name={rubric_name}")
        found_count += 1

        report["categories"].append(
            {
                "category": category,
                "status": "found",
                "selected_rubric_id": rubric_id,
                "selected_rubric_name": rubric_name,
                "aliases": aliases,
                "candidates": candidates,
            }
        )

    _save_candidates(report)

    if dry_run:
        print("")
        print("dry-run: config.yaml не изменён.")
    else:
        _save_config(config)
        print("")
        print(f"config.yaml обновлён: {CONFIG_PATH}")

    print(f"Кандидаты сохранены: {CANDIDATES_PATH}")
    print("")
    print(f"Готово. Найдено: {found_count}. Не найдено: {failed_count}.")


def main() -> int:
    args = sys.argv[1:]

    city = "Ижевск"
    dry_run = "--dry-run" in args

    if "--city" in args:
        index = args.index("--city")
        if index + 1 >= len(args):
            raise RuntimeError("После --city нужно указать город.")
        city = args[index + 1]

    build_rubrics(
        city=city,
        dry_run=dry_run,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())