from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from geo_analyzer.benchmarks.city_benchmark import get_or_create_city_benchmark
from geo_analyzer.core.logger import get_logger
from geo_analyzer.core.models import LocationInput, ResolvedLocation
from geo_analyzer.core.settings import get_settings
from geo_analyzer.enrichment.validators import validate_location_input
from geo_analyzer.geometry.isochrones import build_isochrones
from geo_analyzer.geometry.spatial_join import attach_isochrone_counts, build_poi_details_by_isochrones
from geo_analyzer.ingestion.dgis.city_center_resolver import resolve_city_center
from geo_analyzer.ingestion.dgis.geocoder import DGISGeocoder
from geo_analyzer.ingestion.dgis.places_enriched_loader import load_places_near_point
from geo_analyzer.ingestion.dgis.preflight import run_dgis_preflight
from geo_analyzer.ingestion.dgis.region_runtime_patch import ENV_REGION_ID, ENV_REGION_NAME
from geo_analyzer.ingestion.dgis.routing_loader import get_drive_metrics
from geo_analyzer.metrics.accessibility import build_accessibility_snapshot
from geo_analyzer.metrics.anti_driver_score import build_anti_driver_summary, calculate_anti_driver_penalty, detect_anti_drivers
from geo_analyzer.metrics.attraction import compute_attraction_score
from geo_analyzer.metrics.benchmarking import build_benchmark_summary
from geo_analyzer.metrics.centrality import build_network_metrics
from geo_analyzer.metrics.density import build_category_summary
from geo_analyzer.metrics.environment_quality import build_quality_scores
from geo_analyzer.metrics.parking_supply import calculate_parking_supply
from geo_analyzer.pipeline.context_builder import build_analysis_context
from geo_analyzer.reporting.excel_report import export_report_to_excel
from geo_analyzer.reporting.summary_writer import build_text_summary, export_text_summary
from geo_analyzer.visualization.export_assets import export_visuals

try:
    from geo_analyzer.enrichment.categories import classify_pois
except ImportError:
    from geo_analyzer.enrichment.poi_classifier import classify_pois

logger = get_logger("geo_analyzer.pipeline.compact")
ProgressCallback = Callable[..., None] | None


def progress(cb: ProgressCallback, step: int, total: int, msg: str) -> None:
    print(f"[{step}/{total}] {msg}", flush=True)
    if cb is None:
        return
    try:
        cb(step, total, msg)
    except TypeError:
        try:
            cb(msg)
        except Exception:
            pass
    except Exception:
        pass


def done(name: str, started: float) -> None:
    print(f"[OK] {name}: {time.perf_counter() - started:.2f} сек", flush=True)


def _bind_runtime_region(location: ResolvedLocation) -> None:
    region_id = str(location.region_id or "").strip()
    if region_id:
        os.environ[ENV_REGION_ID] = region_id
        os.environ[ENV_REGION_NAME] = str(location.region_name or "").strip()
        logger.info("2GIS runtime region bound from geocoder: region_id=%s region_name=%s", region_id, location.region_name or "")
    else:
        os.environ.pop(ENV_REGION_ID, None)
        os.environ.pop(ENV_REGION_NAME, None)
        logger.warning("2GIS geocoder did not return items.region_id; runtime will probe region by coordinates.")


def resolve_location(location_input: LocationInput) -> ResolvedLocation:
    if location_input.latitude is not None and location_input.longitude is not None:
        return ResolvedLocation(
            latitude=float(location_input.latitude),
            longitude=float(location_input.longitude),
            resolved_address=f"{location_input.latitude}, {location_input.longitude}",
            source_label="coordinates",
        )
    resolved = DGISGeocoder().geocode(str(location_input.address or ""))
    return ResolvedLocation(
        latitude=resolved.latitude,
        longitude=resolved.longitude,
        resolved_address=resolved.resolved_address,
        source_label=resolved.source_label or "address",
        region_id=resolved.region_id,
        region_name=resolved.region_name,
    )


def run_analysis(location_input: LocationInput, *, progress_callback: ProgressCallback = None) -> dict[str, Any]:
    total_started = time.perf_counter()
    total = 14
    validate_location_input(location_input.address, location_input.latitude, location_input.longitude)
    settings = get_settings()

    progress(progress_callback, 1, total, "Геокодирование через 2GIS")
    started = time.perf_counter(); location = resolve_location(location_input); _bind_runtime_region(location); done("Геокодирование", started)

    progress(progress_callback, 2, total, "Построение контекста")
    started = time.perf_counter(); context = build_analysis_context(location); done("Построение контекста", started)

    progress(progress_callback, 3, total, "Preflight 2GIS: рубрикатор и поля API")
    started = time.perf_counter(); preflight = run_dgis_preflight(location.latitude, location.longitude, settings.poi_radius_m, region_id=location.region_id); done("Preflight 2GIS", started)

    progress(progress_callback, 4, total, "Загрузка и обогащение POI через 2GIS Places")
    started = time.perf_counter(); pois_raw = load_places_near_point(location.latitude, location.longitude, settings.poi_radius_m, region_id=location.region_id); done(f"Загрузка POI ({len(pois_raw)})", started)

    progress(progress_callback, 5, total, "Классификация POI")
    started = time.perf_counter(); pois = classify_pois(pois_raw); done("Классификация POI", started)

    progress(progress_callback, 6, total, "Построение изохрон через 2GIS")
    started = time.perf_counter(); isochrones = build_isochrones(location.latitude, location.longitude, settings.graph_dist_m, settings.isochrone_minutes, settings.walk_speed_kph); done(f"Построение изохрон ({len(isochrones)})", started)

    progress(progress_callback, 7, total, "Привязка POI к изохронам")
    started = time.perf_counter(); poi_counts = attach_isochrone_counts(pois, isochrones); poi_details = build_poi_details_by_isochrones(pois, isochrones); done("Привязка POI", started)

    progress(progress_callback, 8, total, "Автомобильная доступность через 2GIS Routing")
    started = time.perf_counter(); center = resolve_city_center(location); drive = get_drive_metrics(location.latitude, location.longitude, center_latitude=center.get("latitude"), center_longitude=center.get("longitude"), center_name=center.get("name"), center_city=center.get("city"), center_source=center.get("source")); done("Автомобильная доступность", started)

    progress(progress_callback, 9, total, "Расчёт доступности и сетевых метрик")
    started = time.perf_counter(); access = build_accessibility_snapshot(poi_counts, poi_details, drive_metrics=drive); network = build_network_metrics(isochrones, poi_counts); done("Доступность и сеть", started)

    progress(progress_callback, 10, total, "Расчёт антидрайверов")
    started = time.perf_counter(); anti = detect_anti_drivers(poi_details, latitude=location.latitude, longitude=location.longitude, radius_m=settings.poi_radius_m); anti_summary = build_anti_driver_summary(anti); penalty = calculate_anti_driver_penalty(anti_summary); done("Антидрайверы", started)

    progress(progress_callback, 11, total, "Расчёт метрик, парковки и benchmark")
    started = time.perf_counter(); category = build_category_summary(poi_details); attraction_summary, attraction_points = compute_attraction_score(category_summary=category, network_metrics=network, temporal_snapshot=None, poi_details_by_iso=poi_details); parking = calculate_parking_supply(pois=pois, isochrones=isochrones, latitude=location.latitude, longitude=location.longitude, radius_m=settings.poi_radius_m, region_id=location.region_id); quality = build_quality_scores(poi_counts, category, network, anti_driver_penalty=penalty, accessibility_snapshot=access, parking_supply_summary=parking.summary); benchmark = get_or_create_city_benchmark(center.get("city"), quality, force_refresh=bool(settings.refresh_city_benchmark), source_address=location.resolved_address or location.source_label, parameters={}); benchmark_summary = build_benchmark_summary(quality, city_benchmark=benchmark); text = build_text_summary(location=location, category_summary=category, quality_scores=quality, anti_driver_summary=anti_summary, attraction_points=attraction_points, accessibility_snapshot=access); text = f"{text}\n\n{parking.text_summary}" if parking.text_summary else text; done("Метрики", started)

    result = {"context": context, "result_dir": context.result_dir, "report_path": context.report_path, "summary_path": context.summary_path, "meta": {"source_label": location.source_label, "resolved_address": location.resolved_address or location.source_label, "latitude": location.latitude, "longitude": location.longitude, "provider": "2GIS", "region_id": location.region_id, "region_name": location.region_name, "city_center": center, "dgis_preflight": preflight, "parking_summary": parking.text_summary, "parking_gui_label": parking.gui_label}, "dgis_preflight": preflight, "pois_raw": pois_raw, "pois": pois, "isochrones": isochrones, "poi_counts_by_iso": poi_counts, "poi_details_by_iso": poi_details, "accessibility_snapshot": access, "drive_metrics": drive, "network_metrics": network, "anti_drivers": anti, "anti_driver_summary": anti_summary, "category_summary": category, "temporal_snapshot": None, "attraction_summary": attraction_summary, "attraction_points": attraction_points, "quality_scores": quality, "benchmark_summary": benchmark_summary, "city_benchmark": benchmark, "text_summary": text, "parking_supply_summary": parking.summary, "parking_details": parking.parking_details, "residential_details": parking.residential_details, "parking_text_summary": parking.text_summary, "parking_gui_label": parking.gui_label}

    progress(progress_callback, 12, total, "Визуализация")
    started = time.perf_counter(); visuals = export_visuals(result, context.images_dir); visuals = visuals if isinstance(visuals, dict) else {}; done("Визуализация", started)

    progress(progress_callback, 13, total, "Экспорт Excel и summary")
    started = time.perf_counter(); export_report_to_excel(result, visuals, "", context.report_path); export_text_summary(text, context.summary_path); done("Экспорт", started)

    progress(progress_callback, 14, total, "Сохранение meta.json")
    started = time.perf_counter(); (context.result_dir / "meta.json").write_text(json.dumps(result["meta"], ensure_ascii=False, indent=2, default=str), encoding="utf-8"); done("meta.json", started)

    progress(progress_callback, total, total, "Готово")
    print(f"[DONE] Анализ завершён за {time.perf_counter() - total_started:.2f} сек", flush=True); logger.info("Готово: %s", context.result_dir)
    return result
