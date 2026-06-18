# Зачистка проекта

Этот документ фиксирует, какие файлы можно удалять перед коммитом и сборкой.

## Безопасная команда

Из корня проекта:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_repository.ps1 -All
```

Для проверки без удаления:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_repository.ps1 -All -DryRun
```

## Что можно удалять

- `build/`
- `dist/`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`
- `logs/`
- `cache/parking_supply/`
- `cache/places/`
- `cache/dgis_category_catalog/`
- `data/cache/`
- `data/data/`
- `data/debug/`
- `data/output/`
- `data/benchmarks/`
- `reports/generated/`
- `reports/tmp/`

## Что нельзя удалять без отдельной проверки

- `src/`
- `config/`
- `tests/`
- `scripts/`
- `docs/`
- `README.md`
- `requirements.txt`
- `pyproject.toml`
- `build.spec`

## Что нельзя коммитить

- `.env`
- `src/geo_analyzer/core/_embedded_secret.py`
- Excel-отчёты
- ZIP-архивы
- кеши API
- папки сборки `build/` и `dist/`

## Основной принцип

В репозитории должен лежать только исходный код, конфигурация, тесты и документация. Всё, что создаётся при запуске анализа, сборке exe или live-запросах к 2GIS, должно оставаться локально.
