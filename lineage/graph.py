"""lineage 그래프 구성과 탐색 (Streamlit 없이 테스트 가능)."""

from __future__ import annotations

import os
from collections import deque

import networkx as nx


def display_name(schema: str, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def build_graph(relations: list[tuple]) -> nx.DiGraph:
    """db.fetch_relations() 결과 → 방향 그래프.

    노드 속성 type(TABLE|VIEW), 노드 속성 files(이 객체를 만드는 DDL 파일 집합)
    """
    G = nx.DiGraph()
    for s_schema, s_name, s_type, t_schema, t_name, t_type, file_path in relations:
        source, target = display_name(s_schema, s_name), display_name(t_schema, t_name)
        G.add_node(source, type=s_type)
        G.add_node(target, type=t_type)
        G.nodes[source].setdefault("files", set())
        G.nodes[target].setdefault("files", set()).add(file_path)
        G.add_edge(source, target)
    return G


def lineage_subgraph(G: nx.DiGraph, node: str, direction: str = "both") -> nx.DiGraph:
    """direction: upstream(원천 방향) | downstream(영향 방향) | both"""
    nodes = {node}
    if direction in ("upstream", "both"):
        nodes |= nx.ancestors(G, node)
    if direction in ("downstream", "both"):
        nodes |= nx.descendants(G, node)
    return G.subgraph(nodes).copy()


def level_nodes(G: nx.DiGraph, start: str, upstream: bool = False) -> dict[int, list[str]]:
    """start 기준 단계별 객체. 순환이 있어도 각 객체는 가장 가까운 단계에 한 번만 나온다."""
    neighbors = G.predecessors if upstream else G.successors
    levels: dict[int, list[str]] = {}
    visited = {start}
    queue = deque([(start, 0)])
    while queue:
        node, level = queue.popleft()
        if level:
            levels.setdefault(level, []).append(node)
        for nxt in sorted(neighbors(node)):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, level + 1))
    return levels


def object_label(file_path: str) -> str:
    """DDL 파일 이름 → 화면 표시용 라벨.

    SSMS '스크립트 생성' 규칙(<schema>.<object>.<Type>.sql)이면 'usp_load_x - SP' 처럼,
    아니면 파일 이름(확장자 제외)을 그대로 쓴다.
    """
    file_name = os.path.basename(file_path)
    parts = file_name.split(".")
    if len(parts) >= 4:
        object_name, object_type = parts[-3], parts[-2]
        short = {"STOREDPROCEDURE": "SP", "VIEW": "VIEW", "USERDEFINEDFUNCTION": "FN"}.get(
            object_type.upper(), object_type
        )
        return f"{object_name} - {short}"
    return os.path.splitext(file_name)[0]


def object_labels(file_paths) -> list[str]:
    return sorted({object_label(p) for p in file_paths})
