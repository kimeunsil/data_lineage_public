import pytest

from lineage.sql_parser import ObjectName, extract_lineage, parse_name, preprocess_sql, split_statements


def edges(sql):
    return {(str(e.source), str(e.target), e.target_type) for e in extract_lineage(sql)}


# ── 전처리 / 이름 ───────────────────────────────────────────────


def test_comments_and_strings_are_removed():
    sql = "/* FROM a.b */ SELECT 1 -- FROM c.d\nSELECT 'FROM e.f', 'it''s' FROM g.h"
    out = preprocess_sql(sql)
    assert "a.b" not in out and "c.d" not in out and "e.f" not in out
    assert "g.h" in out


def test_block_comment_does_not_eat_preceding_sql():
    sql = "INSERT INTO t.keep SELECT * FROM s.keep\n/* note */ INSERT INTO t.b SELECT * FROM s.b"
    assert ("s.keep", "t.keep", "TABLE") in edges(sql)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("dbo.Orders", ObjectName("dbo", "Orders")),
        ("[dbo].[Order Items]", ObjectName("dbo", "Order Items")),
        ("[ErpDb].[erp].[orders]", ObjectName("erp", "orders")),
        ("srv.db.sch.obj", ObjectName("sch", "obj")),
        ("ErpDb..orders", ObjectName("", "orders")),
        ("orders", ObjectName("", "orders")),
        ("#tmp", ObjectName("", "#tmp")),
    ],
)
def test_parse_name(raw, expected):
    assert parse_name(raw) == expected


def test_go_splits_batches():
    sql = "INSERT INTO t.a SELECT * FROM s.a\nGO\nINSERT INTO t.b SELECT * FROM s.b\ngo 2\n"
    assert edges(sql) == {("s.a", "t.a", "TABLE"), ("s.b", "t.b", "TABLE")}


# ── statement 단위 연결 ─────────────────────────────────────────


def test_statements_in_same_procedure_are_not_cross_linked():
    sql = """
    CREATE PROCEDURE dbo.p AS
    BEGIN
      INSERT INTO stg.a SELECT * FROM erp.a
      INSERT INTO stg.b SELECT * FROM erp.b
    END"""
    assert edges(sql) == {("erp.a", "stg.a", "TABLE"), ("erp.b", "stg.b", "TABLE")}


def test_split_keeps_compound_statements_together():
    sql = preprocess_sql(
        """INSERT INTO t (a) SELECT a FROM s UNION ALL SELECT a FROM s2
        UPDATE x SET a = CASE WHEN b = 1 THEN 2 ELSE 3 END FROM x JOIN y ON 1=1
        ;WITH c AS (SELECT 1 AS a) SELECT * FROM c
        SELECT * FROM z WITH (NOLOCK)"""
    )
    stmts = split_statements(sql)
    assert len(stmts) == 4, stmts


# ── 대상 유형 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("INSERT INTO dw.t SELECT * FROM stg.s", ("stg.s", "dw.t", "TABLE")),
        ("INSERT dw.t SELECT * FROM stg.s", ("stg.s", "dw.t", "TABLE")),
        ("SELECT * INTO dw.t FROM stg.s", ("stg.s", "dw.t", "TABLE")),
        ("UPDATE dw.t SET a = s.a FROM stg.s s WHERE 1=1", ("stg.s", "dw.t", "TABLE")),
        ("CREATE VIEW mart.v AS SELECT * FROM dw.t", ("dw.t", "mart.v", "VIEW")),
        ("CREATE OR ALTER VIEW mart.v AS SELECT * FROM dw.t", ("dw.t", "mart.v", "VIEW")),
        ("ALTER VIEW mart.v AS SELECT * FROM dw.t", ("dw.t", "mart.v", "VIEW")),
    ],
)
def test_target_kinds(sql, expected):
    assert expected in edges(sql)


def test_merge_with_when_clauses():
    sql = """MERGE dw.dim AS T USING stg.src AS S ON T.id = S.id
    WHEN MATCHED THEN UPDATE SET T.n = S.n
    WHEN NOT MATCHED THEN INSERT (id, n) VALUES (S.id, S.n)
    WHEN NOT MATCHED BY SOURCE THEN DELETE;"""
    assert edges(sql) == {("stg.src", "dw.dim", "TABLE")}


def test_update_and_delete_by_alias_resolve_to_table():
    sql = """UPDATE f SET f.a = 1 FROM dw.fact f JOIN dw.dim d ON d.k = f.k
    DELETE x FROM mart.z x JOIN erp.del_list l ON x.id = l.id"""
    assert edges(sql) == {("dw.dim", "dw.fact", "TABLE"), ("erp.del_list", "mart.z", "TABLE")}


# ── 걸러야 하는 것 ──────────────────────────────────────────────


def test_cte_names_are_not_sources():
    sql = """;WITH a AS (SELECT * FROM erp.x), b (k) AS (SELECT k FROM erp.y)
    INSERT INTO dw.t SELECT * FROM a JOIN b ON 1=1"""
    assert edges(sql) == {("erp.x", "dw.t", "TABLE"), ("erp.y", "dw.t", "TABLE")}


def test_cte_inside_view():
    sql = "CREATE VIEW mart.v AS WITH m AS (SELECT * FROM dw.f) SELECT * FROM m"
    assert edges(sql) == {("dw.f", "mart.v", "VIEW")}


def test_table_hints_functions_and_dynamic_sql_ignored():
    sql = """INSERT INTO dw.t SELECT * FROM stg.s WITH (NOLOCK) CROSS APPLY dbo.fn_split(s.v) f
    JOIN dbo.fn_calendar(@d) c ON 1=1
    EXEC('INSERT INTO hidden.t SELECT * FROM hidden.s')"""
    assert edges(sql) == {("stg.s", "dw.t", "TABLE")}


def test_comma_join():
    sql = "INSERT INTO dw.t SELECT * FROM erp.a a, erp.b AS b, erp.c WHERE a.id = b.id"
    assert {e[0] for e in edges(sql)} == {"erp.a", "erp.b", "erp.c"}


def test_insert_exec_does_not_swallow_next_select():
    sql = "INSERT INTO stg.e EXEC dbo.proc\nSELECT a INTO stg.f FROM erp.g"
    assert edges(sql) == {("erp.g", "stg.f", "TABLE")}


# ── 임시 객체 연결 ──────────────────────────────────────────────


def test_temp_table_chain_is_resolved():
    sql = """SELECT * INTO #t FROM erp.orders o JOIN erp.items i ON 1=1
    UPDATE #t SET a = 1 FROM #t JOIN erp.fix f ON 1=1
    INSERT INTO dw.fact SELECT * FROM #t"""
    assert edges(sql) == {
        ("erp.orders", "dw.fact", "TABLE"),
        ("erp.items", "dw.fact", "TABLE"),
        ("erp.fix", "dw.fact", "TABLE"),
    }


def test_table_variable_chain_is_resolved():
    sql = """DECLARE @a TABLE (id INT)
    INSERT INTO @a SELECT id FROM dw.f
    INSERT INTO mart.m SELECT * FROM @a a JOIN dw.d d ON 1=1"""
    assert edges(sql) == {("dw.f", "mart.m", "TABLE"), ("dw.d", "mart.m", "TABLE")}


def test_self_reference_is_dropped():
    sql = "INSERT INTO dw.t SELECT * FROM dw.t JOIN stg.s ON 1=1"
    assert edges(sql) == {("stg.s", "dw.t", "TABLE")}
