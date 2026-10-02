from lineage.graph import build_graph, level_nodes, lineage_subgraph, object_label, object_labels

RELS = [
    ("erp", "a", "TABLE", "stg", "a", "TABLE", "stg/stg.usp_a.StoredProcedure.sql"),
    ("stg", "a", "TABLE", "dw", "f", "TABLE", "dw/dw.usp_f.StoredProcedure.sql"),
    ("stg", "b", "TABLE", "dw", "f", "TABLE", "dw/dw.usp_f.StoredProcedure.sql"),
    ("dw", "f", "TABLE", "mart", "v", "VIEW", "mart/mart.v.View.sql"),
    ("", "raw", "TABLE", "stg", "b", "TABLE", "load_b.sql"),
]


def test_build_graph_attributes():
    G = build_graph(RELS)
    assert G.nodes["mart.v"]["type"] == "VIEW"
    assert G.nodes["dw.f"]["files"] == {"dw/dw.usp_f.StoredProcedure.sql"}
    assert G.nodes["erp.a"]["files"] == set()
    assert "raw" in G  # 스키마 없는 객체는 이름만


def test_subgraph_directions():
    G = build_graph(RELS)
    assert set(lineage_subgraph(G, "dw.f", "upstream")) == {"dw.f", "stg.a", "stg.b", "erp.a", "raw"}
    assert set(lineage_subgraph(G, "dw.f", "downstream")) == {"dw.f", "mart.v"}
    assert len(lineage_subgraph(G, "dw.f")) == 6


def test_levels_up_and_down():
    G = build_graph(RELS)
    assert level_nodes(G, "dw.f", upstream=True) == {1: ["stg.a", "stg.b"], 2: ["erp.a", "raw"]}
    assert level_nodes(G, "erp.a") == {1: ["stg.a"], 2: ["dw.f"], 3: ["mart.v"]}


def test_levels_with_cycle():
    G = build_graph([("x", "a", "TABLE", "x", "b", "TABLE", "f"), ("x", "b", "TABLE", "x", "a", "TABLE", "g")])
    assert level_nodes(G, "x.a") == {1: ["x.b"]}


def test_object_label():
    assert object_label("dw/dw.usp_load_fact.StoredProcedure.sql") == "usp_load_fact - SP"
    assert object_label("mart.v_x.View.sql") == "v_x - VIEW"
    assert object_label("adhoc/load_b.sql") == "load_b"
    assert object_labels(["a/x.sql", "b/x.sql"]) == ["x"]
