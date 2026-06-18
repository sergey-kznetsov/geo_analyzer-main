import networkx as nx

from geo_analyzer.geometry.travel_time import add_walk_travel_time


def test_add_walk_travel_time_sets_edge_attr():
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, length=120)
    result = add_walk_travel_time(graph, speed_kph=4.8)
    data = list(result.edges(keys=True, data=True))[0][3]
    assert "travel_time_min" in data
    assert data["travel_time_min"] > 0
