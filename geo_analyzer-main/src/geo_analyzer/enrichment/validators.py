from __future__ import annotations


def validate_location_input(address: str | None, latitude: float | None, longitude: float | None) -> None:
    """Проверяет пользовательский ввод.

    Допустимы два режима:
    1. address;
    2. latitude + longitude.
    """
    has_address = bool(str(address).strip()) if address is not None else False
    has_coordinates = latitude is not None and longitude is not None

    if not has_address and not has_coordinates:
        raise ValueError("Нужно передать либо адрес, либо координаты.")

    if has_coordinates:
        lat = float(latitude)
        lon = float(longitude)

        if not -90 <= lat <= 90:
            raise ValueError("Широта должна быть в диапазоне от -90 до 90.")

        if not -180 <= lon <= 180:
            raise ValueError("Долгота должна быть в диапазоне от -180 до 180.")