from __future__ import annotations


def add_walk_travel_time(graph, speed_kph: float):
    """Добавляет время прохода по ребру в минутах."""
    meters_per_minute = speed_kph * 1000 / 60
    for _, _, _, data in graph.edges(keys=True, data=True):
        length_m = float(data.get("length", 0) or 0)
        data["travel_time_min"] = length_m / meters_per_minute if meters_per_minute else None
    return graph
