from __future__ import annotations

from abc import ABC, abstractmethod
from geo_analyzer.core.models import ResolvedLocation


class BaseGeocoder(ABC):
    @abstractmethod
    def geocode(self, address: str) -> ResolvedLocation:
        """Возвращает координаты и нормализованный адрес."""

    @abstractmethod
    def reverse_geocode(self, latitude: float, longitude: float) -> str | None:
        """Возвращает адрес по координатам."""
