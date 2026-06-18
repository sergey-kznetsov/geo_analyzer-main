"""Подсистема конкурентного анализа рынка жилья.

Отдельный самодостаточный модуль (по аналогии с ``geo_analyzer.parking``):
от основного анализатора берёт только координаты и радиус, а данные по
конкурентам — новым и недавно построенным/строящимся ЖК — собирает сам из
2GIS, кеша мест и benchmark-снапшотов.

Публичный вход — :func:`analyze_competition`.
"""

from __future__ import annotations

from geo_analyzer.competition.competitors import (
    COMPETITION_LOADER_VERSION,
    CompetitionResult,
    analyze_competition,
)

__all__ = [
    "COMPETITION_LOADER_VERSION",
    "CompetitionResult",
    "analyze_competition",
]
