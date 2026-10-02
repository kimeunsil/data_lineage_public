"""DDL 디렉터리를 읽어 lineage 메타 DB 를 만든다.

python -m lineage.extract --ddl-dir ddl --db data_lineage.db [--default-schema dbo] [-v]
"""

from __future__ import annotations

import argparse
import codecs
import logging
from dataclasses import replace
from pathlib import Path

from lineage import db
from lineage.sql_parser import Edge, extract_lineage

log = logging.getLogger("lineage")


def read_sql_file(path: Path) -> str:
    """SSMS '스크립트 생성' 파일(UTF-16 LE/BE BOM), UTF-8(BOM 유무), CP949(한글 주석)를 순서대로 판별."""
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def apply_default_schema(edges: list[Edge], default_schema: str) -> list[Edge]:
    """스키마 없이 쓴 객체(Orders)를 기본 스키마(dbo.Orders)와 같은 객체로 맞춘다."""
    if not default_schema:
        return edges

    def fix(obj):
        return obj if obj.schema else replace(obj, schema=default_schema)

    return [Edge(fix(e.source), fix(e.target), e.target_type) for e in edges]


def scan(ddl_dir: Path, default_schema: str = "") -> list[tuple[str, list[Edge]]]:
    results = []
    for path in sorted(ddl_dir.rglob("*.sql")):
        rel = path.relative_to(ddl_dir).as_posix()
        edges = apply_default_schema(extract_lineage(read_sql_file(path)), default_schema)
        if not edges:
            log.warning("관계 없음: %s", rel)
        for e in edges:
            log.debug("%s: %s -> %s (%s)", rel, e.source, e.target, e.target_type)
        results.append((rel, edges))
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DDL 파일에서 테이블 lineage 를 추출해 SQLite 에 저장")
    p.add_argument("--ddl-dir", default="ddl", type=Path)
    p.add_argument("--db", default="data_lineage.db", type=Path)
    p.add_argument("--default-schema", default="", help="스키마 없는 객체에 붙일 스키마 (예: dbo). 기본은 ''")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    if not args.ddl_dir.is_dir():
        p.error(f"DDL 디렉터리가 없습니다: {args.ddl_dir}")

    conn = db.connect(args.db)
    db.init_schema(conn)
    stats = db.sync_lineage(conn, scan(args.ddl_dir, args.default_schema))
    conn.close()
    print(f"파일 {stats['files']}개 · 관계 {stats['relations']}개 · 객체 {stats['objects']}개 → {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
