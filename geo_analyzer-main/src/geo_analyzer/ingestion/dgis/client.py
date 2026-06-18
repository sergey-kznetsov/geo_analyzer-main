from __future__ import annotations

from typing import Any

import requests

from geo_analyzer.core.exceptions import ExternalServiceError
from geo_analyzer.core.settings import get_settings


class DGISClient:
    """Тонкий HTTP-клиент для 2GIS API."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.session = requests.Session()

    def _check_api_error(self, data: dict[str, Any]) -> None:
        """Проверяет ошибки, которые 2GIS может вернуть внутри JSON при HTTP 200."""
        meta = data.get("meta")

        if isinstance(meta, dict):
            code = meta.get("code")
            error = meta.get("error") or meta.get("message") or meta.get("description")

            try:
                code_int = int(code)
            except (TypeError, ValueError):
                code_int = None

            if code_int is not None and code_int >= 400:
                raise ExternalServiceError(
                    f"2GIS API вернул ошибку code={code_int}: {error or data}"
                )

        error = data.get("error")

        if error:
            raise ExternalServiceError(f"2GIS API вернул ошибку: {error}")

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_params = dict(params or {})
        request_params["key"] = self.settings.dgis_api_key

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=request_params,
                json=json_body,
                timeout=self.settings.dgis_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ExternalServiceError(f"Ошибка запроса к 2GIS API: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ExternalServiceError("2GIS API вернул не-JSON ответ.") from exc

        if not isinstance(data, dict):
            raise ExternalServiceError(f"2GIS API вернул неожиданный ответ: {data}")

        self._check_api_error(data)

        return data

    def get_catalog(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.settings.dgis_catalog_url.rstrip('/')}/{path.lstrip('/')}"
        return self._request("GET", url, params=params)

    def post_routing(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.settings.dgis_routing_url.rstrip('/')}/{path.lstrip('/')}"
        return self._request("POST", url, params=params, json_body=json_body)