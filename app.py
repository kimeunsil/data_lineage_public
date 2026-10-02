"""Data Lineage Explorer (Streamlit)

streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from lineage import db
from lineage.graph import build_graph, level_nodes, lineage_subgraph, object_labels

DB_PATH = os.environ.get("LINEAGE_DB", "data_lineage.db")

COLORS = {"selected": "#E53935", "upstream": "#64B5F6", "downstream": "#81C784"}
SHAPES = {"TABLE": "box", "VIEW": "ellipse"}

st.set_page_config(page_title="Data Lineage Explorer", layout="wide")


@st.cache_data(ttl=300, show_spinner=False)
def load_relations(db_path: str) -> list[tuple]:
    # Streamlit 은 세션마다 스레드가 다르므로 요청마다 연결을 열고 닫는다 (커넥션 공유 없음)
    conn = db.connect(db_path)
    try:
        return db.fetch_relations(conn)
    finally:
        conn.close()


def render_graph(sub, selected: str, upstream: set[str]) -> str:
    net = Network(height="760px", width="100%", directed=True, cdn_resources="remote")
    net.set_options(
        """
        {
          "layout": {"hierarchical": {"enabled": true, "direction": "LR", "sortMethod": "directed",
                                      "levelSeparation": 260, "nodeSpacing": 90}},
          "physics": {"enabled": false},
          "edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.6}}, "color": {"color": "#9E9E9E"},
                    "smooth": {"type": "cubicBezier", "forceDirection": "horizontal"}},
          "interaction": {"hover": true, "navigationButtons": true}
        }
        """
    )
    for node, attrs in sub.nodes(data=True):
        role = "selected" if node == selected else "upstream" if node in upstream else "downstream"
        made_by = object_labels(attrs.get("files", ()))
        title = f"{node}\n유형: {attrs.get('type', 'TABLE')}" + (
            "\n생성: " + ", ".join(made_by) if made_by else "\n(원천 객체)"
        )
        net.add_node(
            node,
            label=node,
            title=title,
            color=COLORS[role],
            shape=SHAPES.get(attrs.get("type"), "box"),
            font={"color": "#FFFFFF" if role == "selected" else "#212121"},
        )
    for source, target in sub.edges():
        net.add_edge(source, target)
    return net.generate_html()


def render_levels(G, levels: dict[int, list[str]], kind: str) -> None:
    if not levels:
        st.caption("없음")
        return
    for level in sorted(levels):
        st.markdown(f"**{level}차 {kind} 객체**")
        for node in levels[level]:
            labels = object_labels(G.nodes[node].get("files", ()))
            suffix = f"  \n<small>{', '.join(labels)}</small>" if labels else ""
            st.markdown(f"- `{node}`{suffix}", unsafe_allow_html=True)


# ── 화면 ─────────────────────────────────────────────────────────
st.title("Data Lineage Explorer")

if not os.path.exists(DB_PATH):
    st.error(f"메타 DB({DB_PATH})가 없습니다. 먼저 `python -m lineage.extract --ddl-dir ddl` 을 실행하세요.")
    st.stop()

relations = load_relations(DB_PATH)
if not relations:
    st.warning("추출된 관계가 없습니다. DDL 디렉터리와 추출 로그를 확인하세요.")
    st.stop()

G = build_graph(relations)
files = {f for _, _, _, _, _, _, f in relations}
c1, c2, c3 = st.columns(3)
c1.metric("객체", G.number_of_nodes())
c2.metric("관계", G.number_of_edges())
c3.metric("DDL 파일", len(files))

left, right = st.columns([3, 2])
selected = left.selectbox("객체 선택 (입력해서 검색)", sorted(G.nodes, key=str.lower))
direction = right.radio(
    "방향",
    ["both", "upstream", "downstream"],
    format_func={"both": "전체", "upstream": "상위(원천)", "downstream": "하위(영향)"}.get,
    horizontal=True,
)

sub = lineage_subgraph(G, selected, direction)
up_levels = level_nodes(G, selected, upstream=True) if direction != "downstream" else {}
down_levels = level_nodes(G, selected) if direction != "upstream" else {}
upstream_nodes = {n for nodes in up_levels.values() for n in nodes}

graph_col, list_col = st.columns([7, 3])
with graph_col:
    components.html(render_graph(sub, selected, upstream_nodes), height=780, scrolling=False)
    st.caption("빨강: 선택 객체 · 파랑: 상위(원천) · 초록: 하위(영향) · 네모: 테이블 · 타원: 뷰")
with list_col:
    st.subheader("계보 목록")
    made_by = object_labels(G.nodes[selected].get("files", ()))
    st.markdown(f"**{selected}** ({G.nodes[selected].get('type')})")
    st.caption("생성: " + (", ".join(made_by) if made_by else "원천 객체"))
    if direction != "downstream":
        st.markdown("#### 상위(원천)")
        render_levels(G, up_levels, "상위")
    if direction != "upstream":
        st.markdown("#### 하위(영향)")
        render_levels(G, down_levels, "하위")
