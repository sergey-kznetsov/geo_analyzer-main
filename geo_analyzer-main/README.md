# Geo Analyzer

Geo Analyzer — Windows-приложение и Python-проект для пространственного анализа локаций на базе 2GIS API.

Проект оценивает инфраструктуру, доступность, качество городской среды, антидрайверы, парковочную обеспеченность и может сравнивать две локации между собой.

## Основные возможности

Приложение выполняет полный анализ адреса: геокодирование, загрузку POI через 2GIS, построение изохрон 5 / 10 / 15 минут, привязку объектов к зонам доступности, расчёт метрик среды, формирование Excel-отчёта, summary и изображений.

В GUI доступны два режима:

- один адрес;
- сравнение двух локаций.

В режиме сравнения приложение запускает обычный pipeline анализа для каждой локации, сохраняет отдельные отчёты и формирует общий `comparison.xlsx`, `comparison_summary.txt`, `comparison_scores.png` и `comparison_map.png`.

## Данные и API

Основной источник данных — 2GIS API. Ключ задаётся через `.env` или встраивается в exe при Windows-сборке.

Файл `.env` в корне проекта:

```env
DGIS_API_KEY=твой_ключ_2gis
```

Сторонние API для новых модулей парковки и сравнения не требуются.

## Запуск из исходников

```bash
python -m venv .venv
. .venv/Scripts/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

Запуск GUI:

```bash
python gui.py
```

Запуск CLI по адресу:

```bash
python main.py --address "Ижевск, Пушкинская 277"
```

Запуск CLI по координатам:

```bash
python main.py --lat 56.8526 --lon 53.2115
```

Запуск без внешних API, только по кешу:

```bash
python main.py --address "Ижевск, Пушкинская 277" --no-api
```

Принудительное обновление кеша и benchmark:

```bash
python main.py --address "Ижевск, Пушкинская 277" --refresh-cache --refresh-city-benchmark
```

## Где сохраняются результаты

При запуске из исходников результаты сохраняются в проекте:

```text
data/output/
cache/
data/benchmarks/
logs/
```

При запуске Windows portable-сборки результаты должны сохраняться внутри распакованной папки приложения:

```text
GeoAnalyzer_windows_portable/
  GeoAnalyzer/
    GeoAnalyzer.exe
    output/
    cache/
    benchmarks/
    logs/
```

Пути можно переопределить переменными окружения:

```env
GEO_ANALYZER_OUTPUT_DIR=C:\GeoAnalyzer\output
GEO_ANALYZER_CACHE_DIR=C:\GeoAnalyzer\cache
GEO_ANALYZER_BENCHMARK_DIR=C:\GeoAnalyzer\benchmarks
GEO_ANALYZER_LOG_DIR=C:\GeoAnalyzer\logs
```

## Структура результата одного анализа

```text
output/<адрес>_<дата_время>/
  report.xlsx
  summary.txt
  meta.json
  images/
  raw/
```

Основные изображения:

```text
infrastructure_map.png
quality_scores_line.png
top_categories_line.png
parking_map.png
```

## Структура результата сравнения

```text
output/comparison_<дата_время>/
  location_a/
  location_b/
  comparison.xlsx
  comparison_summary.txt
  comparison_scores.png
  comparison_map.png
```

Если локации находятся в разных городах, сравнение должно показывать абсолютного победителя и победителя относительно benchmark своего города.

## Парковочная обеспеченность

Модуль парковки рассчитывает обеспеченность внутри зон 0–5, 5–10 и до 10 минут пешком.

В отчёт добавляются листы:

```text
Парковочная обеспеченность
Парковки 2GIS
Жилые дома 2GIS
```

Если данных 2GIS по домам или парковочным местам недостаточно, приложение не должно подставлять недостоверную оценку. В отчёте должен быть понятный статус о неполных данных.

## Windows-сборка

Сборка выполняется из корня проекта:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Сборка без тестов:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -SkipTests
```

После сборки пользовательский архив должен появиться здесь:

```text
dist/GeoAnalyzer_windows_portable.zip
```

Пользователь распаковывает архив и запускает `GeoAnalyzer.exe`. Python на компьютере пользователя не нужен.

## Зачистка проекта перед коммитом

Безопасная зачистка временных файлов:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_repository.ps1 -All
```

Подробнее см. `docs/CLEANUP_AND_DELETE.md`.

## Что не коммитить

Не коммитить `.env`, встроенный файл секрета, папки `build/`, `dist/`, локальные отчёты, кеши, benchmark, debug-дампы API, Excel-файлы и ZIP-архивы.
