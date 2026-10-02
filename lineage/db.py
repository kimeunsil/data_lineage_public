"""메타데이터 DB(SQLite) 접근."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from lineage.sql_parser import TABLE, VIEW, Edge, ObjectName

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema.sql"
RELATION_TYPE = "LINEAGE"


def connect(db_path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))


def get_or_create_object(conn: sqlite3.Connection, obj: ObjectName, object_type: str) -> int:
    """(스키마, 이름)당 객체는 하나만 둔다.

    같은 이름이 원천(TABLE)으로 먼저 등록된 뒤 뷰 정의가 나오면 VIEW 로 승격한다.
    이름 비교는 컬럼의 COLLATE NOCASE 로 대소문자를 무시한다.
    """
    row = conn.execute(
        "SELECT OBJECT_ID, OBJECT_TYPE FROM OBJECT_INFO WHERE OBJECT_NAME = ? AND SCHEMA_NAME = ?",
        (obj.name, obj.schema),
    ).fetchone()
    if row:
        object_id, current_type = row
        if object_type == VIEW and current_type != VIEW:
            conn.execute("UPDATE OBJECT_INFO SET OBJECT_TYPE = ? WHERE OBJECT_ID = ?", (VIEW, object_id))
        return object_id
    cur = conn.execute(
        "INSERT INTO OBJECT_INFO (OBJECT_NAME, OBJECT_TYPE, SCHEMA_NAME) VALUES (?, ?, ?)",
        (obj.name, object_type, obj.schema),
    )
    return cur.lastrowid


def insert_relation(conn: sqlite3.Connection, source_id: int, target_id: int, file_path: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO OBJECT_RELATION (SOURCE_OBJECT_ID, TARGET_OBJECT_ID, RELATION_TYPE, FILE_PATH) "
        "VALUES (?, ?, ?, ?)",
        (source_id, target_id, RELATION_TYPE, file_path),
    )


def sync_lineage(conn: sqlite3.Connection, file_edges: Iterable[tuple[str, list[Edge]]]) -> dict:
    """관계를 현재 DDL 기준으로 통째로 다시 만든다 (한 트랜잭션).

    - 삭제·이름 변경된 DDL 의 옛 관계가 남지 않는다
    - 이미 있는 객체는 OBJECT_ID 를 유지한다
    - 더 이상 어떤 관계에도 쓰이지 않는 객체는 정리한다
    """
    stats = {"files": 0, "relations": 0}
    with conn:
        conn.execute("DELETE FROM OBJECT_RELATION")
        for file_path, edges in file_edges:
            stats["files"] += 1
            for e in edges:
                # 원천은 TABLE 로 등록하되, 이미 VIEW 로 알려진 객체면 그대로 VIEW
                source_id = get_or_create_object(conn, e.source, TABLE)
                target_id = get_or_create_object(conn, e.target, e.target_type)
                insert_relation(conn, source_id, target_id, file_path)
        conn.execute(
            "DELETE FROM OBJECT_INFO WHERE OBJECT_ID NOT IN "
            "(SELECT SOURCE_OBJECT_ID FROM OBJECT_RELATION UNION SELECT TARGET_OBJECT_ID FROM OBJECT_RELATION)"
        )
    stats["relations"] = conn.execute("SELECT COUNT(*) FROM OBJECT_RELATION").fetchone()[0]
    stats["objects"] = conn.execute("SELECT COUNT(*) FROM OBJECT_INFO").fetchone()[0]
    return stats


def fetch_relations(conn: sqlite3.Connection) -> list[tuple]:
    """(원천 스키마, 원천 이름, 원천 유형, 대상 스키마, 대상 이름, 대상 유형, 파일 경로)"""
    return conn.execute(
        """
        SELECT s.SCHEMA_NAME, s.OBJECT_NAME, s.OBJECT_TYPE,
               t.SCHEMA_NAME, t.OBJECT_NAME, t.OBJECT_TYPE,
               r.FILE_PATH
        FROM OBJECT_RELATION r
        JOIN OBJECT_INFO s ON r.SOURCE_OBJECT_ID = s.OBJECT_ID
        JOIN OBJECT_INFO t ON r.TARGET_OBJECT_ID = t.OBJECT_ID
        """
    ).fetchall()
