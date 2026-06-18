class GeoAnalyzerError(Exception):
    """Базовая ошибка проекта."""


class ConfigurationError(GeoAnalyzerError):
    """Ошибка конфигурации или секретов."""


class ExternalServiceError(GeoAnalyzerError):
    """Ошибка внешнего сервиса или API."""
