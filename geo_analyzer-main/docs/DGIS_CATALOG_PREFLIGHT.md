# 2GIS catalog and preflight mode

Geo Analyzer separates normal analysis data from the official 2GIS regional rubric catalog.

## Main rule

`--refresh-cache` refreshes analysis data, POI responses, routes, parking/building data and other runtime caches. It does not refresh the 2GIS rubric catalog.

The rubric catalog is regional. For every analysed location Geo Analyzer first resolves coordinates, then asks 2GIS for the actual `region_id` of that point. The cached catalog key includes this `region_id`, so reports can be built for different cities without hard binding to Izhevsk or any other default city.

## When catalog is loaded

If the catalog for the detected `region_id` already exists in cache, it is reused.

If the catalog for the detected `region_id` does not exist, Geo Analyzer downloads it once from 2GIS and saves it.

If forced refresh is enabled, Geo Analyzer downloads the catalog again for the detected `region_id` and overwrites the cached copy.

## Manual refresh controls

CLI:

```powershell
python main.py --address "Ижевск, Колизей, жилой комплекс" --refresh-cache
```

Refreshes analysis data only. The 2GIS catalog is reused from cache when available.

```powershell
python main.py --address "Ижевск, Колизей, жилой комплекс" --refresh-cache --refresh-dgis-catalog
```

Refreshes analysis data and forces the official 2GIS catalog to reload for the location region.

GUI:

The checkbox `Обновить каталог 2GIS` controls only the catalog refresh. Keep it disabled for normal runs. Enable it when:

- a new city/region is being analysed for the first time and you want to force a fresh catalog;
- 2GIS changed rubric names or fields;
- debug profiles show missing or outdated category structure.

## Multi-city comparison

For comparison mode, the same catalog-refresh flag applies to both locations. Each location still resolves its own `region_id` and uses its own regional catalog cache.

Example: if Location A is in Izhevsk and Location B is in Perm, the app resolves and stores two separate catalogs, one per 2GIS region.

## Debug output

Technical files are saved under:

```text
data/logs/dgis_api_profiles/
```

The Excel report remains business-readable. Technical fields, raw API payloads, attribute path profiles and rubric snapshots are kept in debug files, not in report sheets.
