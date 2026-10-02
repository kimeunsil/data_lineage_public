"""정규식 기반 T-SQL lineage 추출.

처리 순서
1. 전처리   : 주석·문자열 리터럴 제거, GO 로 배치 분리
2. 구문 분리 : 괄호/CASE 깊이를 추적하며 최상위 키워드 기준으로 statement 단위로 자른다
3. 구문 분석 : statement 마다 대상(INSERT/UPDATE/DELETE/MERGE/SELECT INTO/CREATE VIEW)과
               원천(FROM/JOIN/USING, 콤마 조인)을 찾고 별칭·CTE·함수를 걸러낸다
4. 임시 객체 : #temp / @table 변수를 거쳐 가는 흐름은 원래 원천 → 최종 대상으로 이어 붙인다

같은 파일 안이라도 서로 다른 statement 의 원천과 대상은 연결하지 않는다.
동적 SQL(EXEC('...'), sp_executesql)은 문자열이므로 분석 대상이 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TABLE = "TABLE"
VIEW = "VIEW"

# ── 식별자 ───────────────────────────────────────────────────────
_PART = r'(?:\[[^\]]+\]|"[^"]+"|[A-Za-z_@#][\w@#$]*)'
QNAME = rf"{_PART}(?:\.{{1,2}}{_PART})*"  # [srv].[db].[schema].[obj], db..obj

# 객체 이름 자리에 와도 실제 객체가 아닌 키워드
_RESERVED = {
    "SET", "WHEN", "FROM", "WHERE", "OUTPUT", "TOP", "SELECT", "VALUES", "DEFAULT", "AS", "ON",
    "INTO", "USING", "WITH", "AND", "OR", "NOT", "EXEC", "EXECUTE", "OPENQUERY", "OPENROWSET",
    "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "JOIN", "GROUP", "ORDER", "HAVING", "UNION",
    "EXCEPT", "INTERSECT", "OPTION", "APPLY", "PIVOT", "UNPIVOT", "LATERAL", "THEN", "ELSE", "END",
    "BEGIN", "IF", "WHILE", "RETURN", "DECLARE", "TABLE", "VIEW", "PROCEDURE", "PROC", "FUNCTION",
    "DATABASE", "STATISTICS", "MATCHED", "BY", "TARGET", "SOURCE", "NOLOCK", "CURRENT", "OF",
}  # fmt: skip

_NOT_RESERVED = r"(?!(?:" + "|".join(sorted(_RESERVED)) + r")\b)"
_ALIAS = rf"(?:\s+(?:AS\s+)?{_NOT_RESERVED}(?P<alias>{_PART}))?"

# ── 1. 전처리 ────────────────────────────────────────────────────
_NOISE = re.compile(r"/\*.*?\*/|--[^\n]*|N?'(?:[^']|'')*'", re.S)
_GO = re.compile(r"^\s*GO(?:\s+\d+)?\s*;?\s*$", re.I | re.M)


def preprocess_sql(sql_text: str) -> str:
    """주석은 공백으로, 문자열 리터럴은 빈 문자열('')로 바꾼다.

    하나의 정규식으로 동시에 처리해야 문자열 안의 '--' 나 주석 안의 따옴표에 속지 않는다.
    """

    def repl(m: re.Match) -> str:
        token = m.group(0)
        return "''" if token.endswith("'") and not token.startswith(("--", "/*")) else " "

    return _NOISE.sub(repl, sql_text)


def split_batches(sql_text: str) -> list[str]:
    return [b for b in _GO.split(sql_text) if b.strip()]


# ── 2. 구문 분리 ─────────────────────────────────────────────────
_TOKEN = re.compile(r'\[[^\]]*\]|"[^"]*"|[A-Za-z_@#][\w@#$]*|[();,=.]|\S')
_STARTERS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "SELECT", "WITH", "CREATE", "ALTER", "DROP", "TRUNCATE",
    "DECLARE", "SET", "IF", "ELSE", "WHILE", "BEGIN", "END", "EXEC", "EXECUTE", "RETURN", "PRINT",
    "OPEN", "FETCH", "CLOSE", "DEALLOCATE", "RAISERROR", "THROW", "COMMIT", "ROLLBACK", "GOTO", "USE",
}  # fmt: skip
_DML = {"INSERT", "UPDATE", "DELETE", "MERGE", "SELECT"}
_SET_OPERATORS = {"UNION", "ALL", "EXCEPT", "INTERSECT"}
_CTE_START = re.compile(rf"WITH\s+{_PART}\s*(?:\([^()]*\)\s*)?AS\s*\(", re.I)


def split_statements(batch: str) -> list[str]:
    """최상위(괄호 밖, CASE 밖) 키워드를 기준으로 statement 를 나눈다.

    한 statement 로 이어 붙이는 경우
      INSERT ... SELECT / INSERT ... EXEC          UPDATE ... SET ... FROM
      WITH cte AS (...) <DML>                      CREATE VIEW ... AS [WITH ...] SELECT
      SELECT ... UNION [ALL] SELECT                MERGE ... ;  (WHEN 절 안의 INSERT/UPDATE/DELETE)
      DECLARE c CURSOR FOR SELECT                  FROM t WITH (NOLOCK)  ← 테이블 힌트
    """
    statements: list[str] = []
    start: int | None = None
    depth = case_depth = 0
    head: str | None = None  # 현재 statement 의 주 키워드
    seen_select = seen_set = is_view = False
    prev = ""

    def close(end: int) -> None:
        nonlocal start, head, seen_select, seen_set, is_view
        if start is not None and batch[start:end].strip():
            statements.append(batch[start:end].strip())
        start, head, seen_select, seen_set, is_view = None, None, False, False, False

    for m in _TOKEN.finditer(batch):
        tok, word = m.group(0), m.group(0).upper()

        if tok == "(":
            depth += 1
        elif tok == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and tok == ";":
            close(m.start())
            prev = ";"
            continue
        elif depth == 0 and word == "CASE":
            case_depth += 1
        elif depth == 0 and word == "END" and case_depth:
            case_depth -= 1
            prev = word
            continue

        if depth or case_depth or word not in _STARTERS or prev == ".":
            if start is None and tok.strip():
                start = m.start()
            if word in ("SELECT", "VALUES") and depth == 0:
                seen_select = True
            if word == "VIEW" and head in ("CREATE", "ALTER"):
                is_view = True
            prev = word
            continue

        # ── 최상위 키워드: 새 statement 인지, 현재 statement 의 일부인지 판단 ──
        continues = False
        if head == "MERGE":
            continues = True  # MERGE 는 반드시 ; 로 끝난다
        elif word == "WITH" and not _CTE_START.match(batch, m.start()):
            continues = True  # 테이블 힌트, WITH SCHEMABINDING 등
        elif head == "WITH" and word in _DML:
            continues, head = True, word  # CTE 뒤의 본문
        elif word == "SELECT" and (
            prev in _SET_OPERATORS
            or prev == "FOR"
            or (head == "INSERT" and not seen_select)
            or (is_view and not seen_select)
        ):
            continues = True
        elif word == "WITH" and is_view and not seen_select:
            continues = True
        elif word in ("EXEC", "EXECUTE") and head == "INSERT" and not seen_select:
            continues = True
        elif word == "SET" and head == "UPDATE" and not seen_set:
            continues, seen_set = True, True

        if continues:
            if word in ("SELECT", "EXEC", "EXECUTE"):
                seen_select = True  # INSERT 의 데이터 공급부는 하나뿐
        else:
            close(m.start())
            start, head = m.start(), word
            if word == "SELECT":
                seen_select = True
        prev = word

    close(len(batch))
    return statements


# ── 3. 구문 분석 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ObjectName:
    schema: str  # 스키마가 없으면 ''
    name: str

    @property
    def is_transient(self) -> bool:
        """#temp, ##global_temp, @table_variable"""
        return self.name.startswith(("#", "@"))

    def __str__(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def parse_name(qname: str) -> ObjectName | None:
    """[db].[dbo].[Orders] → ObjectName('dbo', 'Orders'). DB/서버 이름은 버린다."""
    segments, current = [], None
    for tok in re.findall(rf"{_PART}|\.", qname):
        if tok == ".":
            segments.append(current or "")
            current = None
        else:
            current = tok[1:-1] if tok[0] in '["' else tok
    segments.append(current or "")
    name = segments[-1]
    if not name or name.upper() in _RESERVED:
        return None
    schema = segments[-2] if len(segments) >= 2 else ""
    return ObjectName(schema, name)


def _key(obj: ObjectName) -> tuple[str, str]:
    return obj.schema.lower(), obj.name.lower()


# 별칭 자리에 키워드(JOIN, WHERE 등)가 오면 별칭으로 먹지 않도록 부정 전방탐색
_FROM_ITEM = re.compile(rf"\b(?P<kw>FROM|JOIN|USING)\s+(?P<name>{QNAME})(?P<call>\s*\()?{_ALIAS}", re.I)
_COMMA_ITEM = re.compile(rf"\s*,\s*(?P<name>{QNAME})(?P<call>\s*\()?{_ALIAS}", re.I)
_CTE_NAME = re.compile(rf"(?:\bWITH|,)\s*(?P<name>{_PART})\s*(?:\([^()]*\)\s*)?AS\s*\(", re.I)

_TARGET_PATTERNS = [
    (re.compile(rf"\bINSERT\s+(?:TOP\s*\([^)]*\)\s*)?(?:INTO\s+)?(?P<name>{QNAME})", re.I), TABLE, False),
    (re.compile(rf"\bMERGE\s+(?:TOP\s*\([^)]*\)\s*)?(?:INTO\s+)?(?P<name>{QNAME})", re.I), TABLE, False),
    (re.compile(rf"\bUPDATE\s+(?:TOP\s*\([^)]*\)\s*)?(?P<name>{QNAME})", re.I), TABLE, True),
    (re.compile(rf"\bDELETE\s+(?:TOP\s*\([^)]*\)\s*)?(?:FROM\s+)?(?P<name>{QNAME})", re.I), TABLE, True),
    (re.compile(rf"\bCREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+(?P<name>{QNAME})", re.I), VIEW, False),
    (re.compile(rf"\bALTER\s+VIEW\s+(?P<name>{QNAME})", re.I), VIEW, False),
]
_SELECT_INTO = re.compile(rf"(?P<prev>\S+)\s+INTO\s+(?P<name>{QNAME})", re.I)


@dataclass
class StatementLineage:
    sources: set[ObjectName]
    targets: set[tuple[ObjectName, str]]  # (객체, TABLE|VIEW)


def _alias_ok(alias: str | None) -> str | None:
    if not alias:
        return None
    a = alias[1:-1] if alias[0] in '["' else alias
    return None if a.upper() in _RESERVED else a.lower()


def analyze_statement(stmt: str) -> StatementLineage:
    # CTE 는 statement 맨 앞(;WITH ...)뿐 아니라 CREATE VIEW ... AS WITH ... 에도 올 수 있다
    cte_names: set[str] = set()
    first_cte = re.search(r"\b" + _CTE_START.pattern, stmt, re.I)
    if first_cte:
        cte_names = {m.group("name").strip('[]"').lower() for m in _CTE_NAME.finditer(stmt, first_cte.start())}

    # 원천 + 별칭
    aliases: dict[str, ObjectName] = {}
    sources: set[ObjectName] = set()

    def add_source(m: re.Match) -> None:
        if m.group("call"):  # 테이블 반환 함수
            return
        obj = parse_name(m.group("name"))
        if obj is None or (not obj.schema and obj.name.lower() in cte_names):
            return
        sources.add(obj)
        alias = _alias_ok(m.group("alias"))
        if alias:
            aliases[alias] = obj

    for m in _FROM_ITEM.finditer(stmt):
        add_source(m)
        if m.group("kw").upper() == "FROM":  # FROM a, b, c (ANSI-89 조인)
            pos = m.end()
            while (c := _COMMA_ITEM.match(stmt, pos)) is not None:
                add_source(c)
                pos = c.end()

    # 대상
    targets: set[tuple[ObjectName, str]] = set()
    for pattern, obj_type, may_be_alias in _TARGET_PATTERNS:
        for m in pattern.finditer(stmt):
            obj = parse_name(m.group("name"))
            if obj is None:
                continue
            if may_be_alias and not obj.schema and obj.name.lower() in aliases:
                obj = aliases[obj.name.lower()]  # UPDATE t SET ... FROM dbo.orders t
            targets.add((obj, obj_type))
    for m in _SELECT_INTO.finditer(stmt):
        if m.group("prev").upper() in ("INSERT", "MERGE"):
            continue
        obj = parse_name(m.group("name"))
        if obj is not None:
            targets.add((obj, TABLE))

    target_keys = {_key(t) for t, _ in targets}
    sources = {s for s in sources if _key(s) not in target_keys}
    return StatementLineage(sources, targets)


# ── 4. 파일 단위 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Edge:
    source: ObjectName
    target: ObjectName
    target_type: str


def extract_lineage(sql_text: str) -> list[Edge]:
    """SQL 파일 내용 → (원천, 대상) 관계 목록. 임시 객체는 원래 원천으로 이어 붙인다."""
    transient: dict[tuple[str, str], set[ObjectName]] = {}  # #tmp → 실제 원천들
    edges: set[Edge] = set()

    for batch in split_batches(preprocess_sql(sql_text)):
        for stmt in split_statements(batch):
            lin = analyze_statement(stmt)
            if not lin.targets:
                continue
            resolved: set[ObjectName] = set()
            for s in lin.sources:
                if s.is_transient:
                    resolved |= transient.get(_key(s), set())
                else:
                    resolved.add(s)
            for target, obj_type in lin.targets:
                if target.is_transient:
                    transient.setdefault(_key(target), set()).update(resolved)
                    continue
                for s in resolved:
                    if _key(s) != _key(target):
                        edges.add(Edge(s, target, obj_type))

    return sorted(edges, key=lambda e: (str(e.target).lower(), str(e.source).lower()))
