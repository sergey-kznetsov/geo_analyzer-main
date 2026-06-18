from __future__ import annotations

import argparse
import os
import sys

from geo_analyzer.core.models import LocationInput
from geo_analyzer.ingestion.dgis.diagnostics_v2 import check_dgis_key, format_dgis_key_check
from geo_analyzer.ingestion.dgis.preflight import DGISAuthorizationError
from geo_analyzer.pipeline.analysis_pipeline import run_analysis
from geo_analyzer.pipeline.competition_pipeline import run_competition_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Geo Analyzer")

    parser.add_argument(
        "--address",
        type=str,
        help="Адрес для анализа",
    )
    parser.add_argument(
        "--lat",
        type=float,
        help="Широта точки",
    )
    parser.add_argument(
        "--lon",
        type=float,
        help="Долгота точки",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Не делать внешние API-запросы. Использовать только кеш.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Обновить кеш внешних данных анализа. Каталог рубрик 2GIS не обновляет.",
    )
    parser.add_argument(
        "--refresh-dgis-catalog",
        action="store_true",
        help="Принудительно обновить официальный каталог рубрик 2GIS для региона.",
    )
    parser.add_argument(
        "--refresh-city-benchmark",
        action="store_true",
        help="Принудительно обновить локальный benchmark snapshot города.",
    )
    parser.add_argument(
        "--check-dgis-key",
        action="store_true",
        help="Проверить активный DGIS_API_KEY на geocoder region_id, rubric/list и Places API без запуска отчёта.",
    )
    parser.add_argument(
        "--competition",
        action="store_true",
        help="Запустить отдельный конкурентный анализ (ЖК/новостройки вокруг точки) вместо полного анализа.",
    )

    return parser


def _looks_like_dgis_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "2gis" in text and ("authorization" in text or "dgis_api_key" in text or "key" in text or "apikey" in text)


def _set_env_flags(args: argparse.Namespace) -> None:
    if args.no_api:
        os.environ["GEO_ANALYZER_NO_API"] = "1"

    if args.refresh_cache:
        os.environ["GEO_ANALYZER_REFRESH_CACHE"] = "1"

    os.environ["GEO_ANALYZER_REFRESH_DGIS_CATALOG"] = "1" if args.refresh_dgis_catalog else os.environ.get("GEO_ANALYZER_REFRESH_DGIS_CATALOG", "0")

    if args.refresh_city_benchmark:
        os.environ["GEO_ANALYZER_REFRESH_CITY_BENCHMARK"] = "1"


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _set_env_flags(args)

    if args.check_dgis_key:
        latitude = args.lat if args.lat is not None else 56.853003
        longitude = args.lon if args.lon is not None else 53.199365
        result = check_dgis_key(latitude=latitude, longitude=longitude, address=args.address)
        print(format_dgis_key_check(result))
        return 0 if result.get("ok") else 1

    if args.address:
        location_input = LocationInput(address=args.address)
    elif args.lat is not None and args.lon is not None:
        location_input = LocationInput(latitude=args.lat, longitude=args.lon)
    else:
        parser.error("Нужно передать либо --address, либо одновременно --lat и --lon.")
        return 2

    try:
        if args.competition:
            run_competition_analysis(location_input)
            return 0

        run_analysis(location_input)
        return 0
    except DGISAuthorizationError as exc:
        print(f"Ошибка авторизации 2GIS: {exc}", file=sys.stderr)
        print("Проверь активный ключ командой: python main.py --check-dgis-key --address \"Пермь, Ленина 50\"", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        if _looks_like_dgis_auth_error(exc):
            print(f"Ошибка авторизации 2GIS: {exc}", file=sys.stderr)
            print("Проверь активный ключ командой: python main.py --check-dgis-key --address \"Пермь, Ленина 50\"", file=sys.stderr)
            return 1
        raise
