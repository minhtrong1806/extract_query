from __future__ import annotations

from typing import List, Tuple

from sqlglot import expressions as exp

from ast_utils import iter_selects
from extractor import _extract_rows_from_select, extract_schema_table_column_rows
from parser import parse_select_statement


def _extract_rows(sql: str, sql_text: str | None = None) -> List[dict[str, str]]:
    parse_result = parse_select_statement(sql)
    assert parse_result.error is None
    assert isinstance(parse_result.ast, exp.Expression)
    return extract_schema_table_column_rows(
        parse_result.ast,
        parse_result.placeholder_map,
        sql_text=sql if sql_text is None else sql_text,
    )


def _as_set(rows: List[dict[str, str]]) -> set[Tuple[str, str, str, str, str]]:
    return {
        (
            row.get("SCHEMA", ""),
            row.get("TABLE", ""),
            row.get("COLUMN", ""),
            row.get("REASON", ""),
            row.get("CLAUSE", ""),
        )
        for row in rows
    }


def test_join_with_alias_and_schema():
    """Kiểm tra resolve bảng với alias và schema."""
    sql = """
        SELECT a.col1, b.col2
        FROM sch1.table1 a
        JOIN table2 b ON a.id = b.id
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("sch1", "table1", "col1", "", "PROJECTION") in row_set
    assert ("", "table2", "col2", "", "PROJECTION") in row_set


def test_cte_resolves_to_base_table():
    """CTE phải resolve về bảng gốc trong CTE."""
    sql = """
        WITH cte AS (
            SELECT t.id, t.name
            FROM sch2.t_users t
        )
        SELECT c.id
        FROM cte c
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("sch2", "t_users", "id", "", "PROJECTION") in row_set


def test_union_subquery_returns_multiple_sources():
    """Union trong subquery trả về nhiều nguồn bảng cho cùng cột."""
    sql = """
        SELECT x.col1
        FROM (
            SELECT a.col1 FROM t_a a
            UNION
            SELECT b.col1 FROM t_b b
        ) x
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "t_a", "col1", "", "PROJECTION") in row_set
    assert ("", "t_b", "col1", "", "PROJECTION") in row_set


def test_star_alias_fallback_for_unqualified_column():
    """Star alias fallback áp dụng cho cột không định danh bảng."""
    sql = "SELECT a.*, col1 FROM sch.star_table a"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("sch", "star_table", "col1", "", "PROJECTION") in row_set


def test_quoted_identifiers_schema_table():
    """Nhận diện schema/table với quoted identifiers."""
    sql = 'SELECT t.col FROM "MYSCHEMA"."MYTABLE" t'
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("MYSCHEMA", "MYTABLE", "col", "", "PROJECTION") in row_set


def test_first_table_fallback_for_unqualified_column():
    """Cột không định danh bảng sẽ fallback về bảng đầu tiên."""
    sql = "SELECT col1 FROM t1 JOIN t2 ON t1.id = t2.id"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "t1", "col1", "FIRST_TABLE_FALLBACK", "PROJECTION") in row_set


def test_subquery_any_table_fallback_for_ambiguous_subquery():
    """Fallback SUBQUERY_ANY_TABLE_FALLBACK khi subquery nhiều bảng và cột không match."""
    sql = """
        SELECT col1
        FROM (
            SELECT t1.col2, t2.col3
            FROM t1
            JOIN t2 ON t1.id = t2.id
        ) s
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "t1", "col1", "SUBQUERY_ANY_TABLE_FALLBACK", "PROJECTION") in row_set


def test_subquery_unresolved_reason():
    """Trả về SUBQUERY_UNRESOLVED khi chỉ có subquery và không resolve được."""
    sql = "SELECT col1 FROM (SELECT 1 AS x) s"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "", "col1", "SUBQUERY_SOURCE_ONLY", "PROJECTION") in row_set


def test_unresolved_table_reason_for_unknown_alias():
    """Trả về UNRESOLVED_TABLE khi alias không tồn tại trong nhiều bảng."""
    sql = "SELECT x.col1 FROM t1 JOIN t2 ON t1.id = t2.id"
    rows = _extract_rows(sql, sql_text="")
    row_set = _as_set(rows)
    assert ("", "", "col1", "UNRESOLVED_TABLE", "PROJECTION") in row_set


def test_subquery_join_resolves_nested_column_alias():
    """Truy vết vào subquery trong JOIN để resolve bảng gốc theo tên cột."""
    sql = """
        SELECT s.verify_decision
        FROM (
            SELECT cq1.response AS verify_decision
            FROM (
                SELECT response FROM base_table
            ) cq1
        ) s
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "base_table", "verify_decision", "", "PROJECTION") in row_set


def test_cte_resolves_column_via_joined_subquery():
    """CTE phải truy vết theo tên cột trong subquery join."""
    sql = """
        WITH cte AS (
            SELECT t.code, o.flag
            FROM base_table t
            JOIN other_table o ON t.id = o.id
        )
        SELECT c.code
        FROM cte c
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "base_table", "code", "", "PROJECTION") in row_set


def test_cte_union_all_resolves_base_tables_for_column():
    """CTE UNION ALL phải resolve về bảng gốc thay vì tên CTE."""
    sql = """
        WITH revert AS (
            SELECT TRANSACTION_ORG_ID, 'CC' CARD_TYPE
            FROM KMDW.FT_SV_CC_TRANS
            WHERE TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
            UNION ALL
            SELECT TRANSACTION_ORG_ID, 'DC' CARD_TYPE
            FROM KMDW.FT_SV_DC_TRANS
            WHERE TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
        ),
        all_trans AS (
            SELECT CUST_NUMBER, TRAN_AMOUNT_BASE, TRANSACTION_ID, TRAN_CURRENCY, 'CC' CARD_TYPE
            FROM KMDW.FT_SV_CC_TRANS A
            WHERE TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
            UNION ALL
            SELECT CUST_NUMBER, TRAN_AMOUNT_BASE, TRANSACTION_ID, TRAN_CURRENCY, 'DC' CARD_TYPE
            FROM KMDW.FT_SV_DC_TRANS A
            WHERE TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
        ),
        base AS (
            SELECT CUST_NUMBER, TRAN_AMOUNT_BASE
            FROM all_trans A
            LEFT JOIN revert B ON A.TRANSACTION_ID = B.TRANSACTION_ORG_ID AND A.CARD_TYPE = B.CARD_TYPE
            WHERE B.TRANSACTION_ORG_ID IS NULL
        )
        SELECT CUST_NUMBER
        FROM base
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("KMDW", "FT_SV_CC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set
    assert ("KMDW", "FT_SV_DC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set


def test_complex_cte_chain_resolves_cust_number():
    """Chuỗi CTE nhiều lớp vẫn resolve về bảng gốc cho CUST_NUMBER."""
    sql = """
        WITH REVERT AS (
            SELECT /*+ MATERIALIZE +*/ TRANSACTION_ORG_ID, 'CC' CARD_TYPE
            FROM KMDW.FT_SV_CC_TRANS
            WHERE 1=1
              AND IS_REVERSAL='Y'
              AND TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
            UNION ALL
            SELECT /*+ MATERIALIZE +*/ TRANSACTION_ORG_ID, 'DC' CARD_TYPE
            FROM KMDW.FT_SV_DC_TRANS
            WHERE 1=1
              AND IS_REVERSAL='Y'
              AND TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
        ),
        REVERT_2 AS (
            SELECT /*+ MATERIALIZE +*/ OPER_ID
            FROM KMDW.FT_SV_VIB_BACKUP_REVERT_DOI_DIEM
        ),
        ALL_TRANS AS (
            SELECT /*+ MATERIALIZE +*/ CUST_NUMBER, TRAN_AMOUNT_BASE, TRANSACTION_ID, TRAN_CURRENCY, 'CC' CARD_TYPE
            FROM KMDW.FT_SV_CC_TRANS A
            WHERE 1=1
              AND TRAN_TYPE = 'OPTP0000'
              AND TRAN_STATUS = 'OPST0400'
              AND DR_CR = 'D'
              AND IS_REVERSAL='N'
              AND TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
            UNION ALL
            SELECT /*+ MATERIALIZE +*/ CUST_NUMBER, TRAN_AMOUNT_BASE, TRANSACTION_ID, TRAN_CURRENCY, 'DC' CARD_TYPE
            FROM KMDW.FT_SV_DC_TRANS A
            WHERE 1=1
              AND TRAN_TYPE = 'OPTP0000'
              AND TRAN_STATUS = 'OPST0400'
              AND DR_CR = 'D'
              AND IS_REVERSAL='N'
              AND TRUNC(POST_DATE) = TO_DATE('#DATEID#', 'YYYY/MM/DD')
        ),
        BASE AS (
            SELECT /*+ MATERIALIZE +*/ CUST_NUMBER, TRAN_AMOUNT_BASE,
                   CASE WHEN TRAN_CURRENCY = 'VND' THEN 1 ELSE 2 END RATIO
            FROM ALL_TRANS A
            LEFT JOIN REVERT B ON A.TRANSACTION_ID = B.TRANSACTION_ORG_ID AND A.CARD_TYPE = B.CARD_TYPE
            LEFT JOIN REVERT_2 C ON A.TRANSACTION_ID = C.OPER_ID
            WHERE 1=1
              AND B.TRANSACTION_ORG_ID IS NULL
              AND C.OPER_ID IS NULL
        )
        SELECT CUST_NUMBER CLIENT_NO,
               SUM(TRUNC(TRAN_AMOUNT_BASE / 1E5)*RATIO) NUM01
        FROM BASE
        GROUP BY CUST_NUMBER
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("KMDW", "FT_SV_CC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set
    assert ("KMDW", "FT_SV_DC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set


def test_derived_column_from_cte_chain_returns_derived_table():
    """Cột sinh ra từ CTE (không gắn bảng gốc) trả về dual."""
    sql = """
        WITH c1 AS (
            SELECT CASE WHEN COUNT(1) >= 2 THEN 'S' ELSE 'F' END STATUS
            FROM base_table
        ),
        check_status AS (
            SELECT c1.STATUS AS STATUS_1 FROM c1
        )
        SELECT STATUS
        FROM (
            SELECT CASE WHEN STATUS_1 = 'S' THEN 'S' ELSE 'F' END STATUS
            FROM check_status
            UNION ALL
            SELECT 'F' STATUS FROM dual
        ) a
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "dual", "STATUS", "DERIVED_COLUMN", "PROJECTION") in row_set
