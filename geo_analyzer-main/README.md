# Geo Analyzer

Geo Analyzer — Windows-приложение и Python-проект для пространственного анализа локаций на базе 2GIS API.

Проект оценивает инфраструктуру, доступность, качество городской среды, антидрайверы и может сравнивать две локации между собой.

## Основные возможности

Приложение выполняет анализ адреса: геокодирование, загрузку POI через 2GIS, построение изохрон 5 / 10 / 15 минут, привязку объектов к зонам доступности, расчёт метрик среды, формирование Excel-отчёта, summary и изображений.

В GUI доступны два режима: один адрес и сравнение двух локаций.

В режиме сравнения приложение запускает обычный pipeline анализа для каждой локации, сохраняет отдельные отчёты и формирует общий `comparison.xlsx`, `comparison_summary.txt`, `comparison_scores.png` и `comparison_map.png`.

## Данные и API

Основной источник данных — 2GIS API. Ключ задаётся через `.env` или встраивается в exe при Windows-сборке.

```env
DGIS_API_KEY=твой_ключ_2gis
```

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

## Результаты

Один анализ сохраняется в `data/output/<адрес>_<дата_время>/` и содержит `report.xlsx`, `summary.txt`, `meta.json`, изображения и raw-данные.

Сравнение сохраняется в `data/output/comparison_<дата_время>/` и содержит папки `location_a`, `location_b`, `comparison.xlsx`, `comparison_summary.txt`, `comparison_scores.png` и `comparison_map.png`.

## Windows-сборка

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Сборка без тестов:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -SkipTests
```

После сборки пользовательский архив должен появиться здесь: `dist/GeoAnalyzer_windows_portable.zip`.

## Зачистка проекта

```powershell
powershell -ExecutionPolicy Bypass -File scripts\clean_repository.ps1 -All
```
