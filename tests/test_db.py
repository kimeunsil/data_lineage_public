import codecs

import pytest

from lineage import db
from lineage.extract import apply_default_schema, main, read_sql_file
from lineage.sql_parser import TABLE, VIEW, Edge, ObjectName

Obj = ObjectName


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_schema(c)
    yield c
    c.close()


def test_foreign_keys_enforced(conn):
    import sqlite3

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO OBJECT_RELATION (SOURCE_OBJECT_ID, TARGET_OBJECT_ID) VALUES (998, 999)")


def test_defaults(conn):
    db.get_or_create_object(conn, Obj("", "orders"), TABLE)
    schema, created = conn.execute("SELECT SCHEMA_NAME, CREATED_DATE FROM OBJECT_INFO").fetchone()
    assert schema == "" and created is not None


def test_object_without_schema_is_not_duplicated(conn):
    a = db.get_or_create_object(conn, Obj("", "orders"), TABLE)
    b = db.get_or_create_object(conn, Obj("", "ORDERS"), TABLE)  # 대소문자 무시
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM OBJECT_INFO").fetchone()[0] == 1


def test_source_table_promoted_to_view(conn):
    a = db.get_or_create_object(conn, Obj("mart", "v"), TABLE)
    b = db.get_or_create_object(conn, Obj("mart", "v"), VIEW)
    assert a == b
    assert conn.execute("SELECT OBJECT_TYPE FROM OBJECT_INFO").fetchone()[0] == VIEW
    # 이후 원천으로 다시 나와도 VIEW 유지
    db.get_or_create_object(conn, Obj("mart", "v"), TABLE)
    assert conn.execute("SELECT OBJECT_TYPE FROM OBJECT_INFO").fetchone()[0] == VIEW


def test_same_edge_from_two_files_is_kept(conn):
    e = Edge(Obj("stg", "s"), Obj("dw", "t"), TABLE)
    stats = db.sync_lineage(conn, [("a.sql", [e]), ("b.sql", [e])])
    assert stats["relations"] == 2
    files = {r[-1] for r in db.fetch_relations(conn)}
    assert files == {"a.sql", "b.sql"}


def test_sync_is_idempotent_and_removes_stale(conn):
    e1 = Edge(Obj("stg", "s"), Obj("dw", "t"), TABLE)
    e2 = Edge(Obj("stg", "old"), Obj("dw", "t"), TABLE)
    db.sync_lineage(conn, [("a.sql", [e1, e2])])
    first_id = conn.execute("SELECT OBJECT_ID FROM OBJECT_INFO WHERE OBJECT_NAME = 't'").fetchone()[0]

    stats = db.sync_lineage(conn, [("a.sql", [e1])])
    assert stats == {"files": 1, "relations": 1, "objects": 2}
    assert conn.execute("SELECT OBJECT_ID FROM OBJECT_INFO WHERE OBJECT_NAME = 't'").fetchone()[0] == first_id
    assert conn.execute("SELECT COUNT(*) FROM OBJECT_INFO WHERE OBJECT_NAME = 'old'").fetchone()[0] == 0


def test_default_schema():
    e = Edge(Obj("", "orders"), Obj("dw", "t"), TABLE)
    assert apply_default_schema([e], "dbo")[0].source == Obj("dbo", "orders")
    assert apply_default_schema([e], "")[0].source == Obj("", "orders")


@pytest.mark.parametrize(
    "data",
    [
        codecs.BOM_UTF16_LE + "-- 한글\nSELECT 1".encode("utf-16-le"),
        codecs.BOM_UTF8 + "-- 한글\nSELECT 1".encode(),
        "-- 한글\nSELECT 1".encode(),
        "-- 한글\nSELECT 1".encode("cp949"),
    ],
)
def test_read_sql_file_encodings(tmp_path, data):
    p = tmp_path / "x.sql"
    p.write_bytes(data)
    assert read_sql_file(p) == "-- 한글\nSELECT 1"


def test_cli_on_sample_ddl(tmp_path, capsys):
    from pathlib import Path

    ddl = Path(__file__).resolve().parents[1] / "ddl"
    assert main(["--ddl-dir", str(ddl), "--db", str(tmp_path / "s.db")]) == 0
    assert "관계" in capsys.readouterr().out
    conn = db.connect(tmp_path / "s.db")
    rel = {(f"{r[0]}.{r[1]}", f"{r[3]}.{r[4]}") for r in db.fetch_relations(conn)}
    # 샘플 DW 의 핵심 흐름
    assert ("erp.orders", "stg.orders") in rel
    assert ("stg.orders", "dw.fact_sales") in rel
    assert ("pos.sales", "dw.fact_sales") in rel  # UNION ALL + 콤마 조인
    assert ("stg.customers", "dw.dim_customer") in rel  # #temp → MERGE
    assert ("dw.fact_sales", "mart.customer_ltv") in rel  # @table 변수 경유 (CP949 파일)
    assert ("mart.v_daily_sales", "mart.v_top_categories") in rel  # 뷰 위의 뷰
    assert not any("audit" in t for _, t in rel)  # 동적 SQL 제외
    assert ("erp.orders", "stg.customers") not in rel  # 다른 statement 끼리 연결 안 함
