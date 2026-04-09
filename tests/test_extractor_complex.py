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


def _as_set(rows: List[dict[str, str]]) -> set[Tuple[str, str, str, str, str, str]]:
    return {
        (
            row.get("CATALOG", ""),
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
    assert ("", "SCH1", "TABLE1", "COL1", "", "PROJECTION") in row_set
    assert ("", "", "TABLE2", "COL2", "", "PROJECTION") in row_set


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
    assert ("", "SCH2", "T_USERS", "ID", "", "PROJECTION") in row_set


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
    assert ("", "", "T_A", "COL1", "", "PROJECTION") in row_set
    assert ("", "", "T_B", "COL1", "", "PROJECTION") in row_set


def test_star_alias_fallback_for_unqualified_column():
    """Star alias fallback áp dụng cho cột không định danh bảng."""
    sql = "SELECT a.*, col1 FROM sch.star_table a"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "SCH", "STAR_TABLE", "COL1", "", "PROJECTION") in row_set


def test_quoted_identifiers_schema_table():
    """Nhận diện schema/table với quoted identifiers."""
    sql = 'SELECT t.col FROM "MYSCHEMA"."MYTABLE" t'
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "MYSCHEMA", "MYTABLE", "COL", "", "PROJECTION") in row_set


def test_first_table_fallback_for_unqualified_column():
    """Cột không định danh bảng sẽ fallback về bảng đầu tiên."""
    sql = "SELECT col1 FROM t1 JOIN t2 ON t1.id = t2.id"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "", "T1", "COL1", "FIRST_TABLE_FALLBACK", "PROJECTION") in row_set


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
    assert ("", "", "T1", "COL1", "SUBQUERY_ANY_TABLE_FALLBACK", "PROJECTION") in row_set


def test_subquery_unresolved_reason():
    """Trả về SUBQUERY_UNRESOLVED khi chỉ có subquery và không resolve được."""
    sql = "SELECT col1 FROM (SELECT 1 AS x) s"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "", "", "COL1", "SUBQUERY_SOURCE_ONLY", "PROJECTION") in row_set


def test_unresolved_table_reason_for_unknown_alias():
    """Trả về UNRESOLVED_TABLE khi alias không tồn tại trong nhiều bảng."""
    sql = "SELECT x.col1 FROM t1 JOIN t2 ON t1.id = t2.id"
    rows = _extract_rows(sql, sql_text="")
    row_set = _as_set(rows)
    assert ("", "", "", "COL1", "UNRESOLVED_TABLE", "PROJECTION") in row_set


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
    assert ("", "", "BASE_TABLE", "VERIFY_DECISION", "", "PROJECTION") in row_set


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
    assert ("", "", "BASE_TABLE", "CODE", "", "PROJECTION") in row_set


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
    assert ("", "KMDW", "FT_SV_CC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set
    assert ("", "KMDW", "FT_SV_DC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set


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
    assert ("", "KMDW", "FT_SV_CC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set
    assert ("", "KMDW", "FT_SV_DC_TRANS", "CUST_NUMBER", "", "PROJECTION") in row_set


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
    assert ("", "", "DUAL", "STATUS", "DERIVED_COLUMN", "PROJECTION") in row_set


def test_cte_alias_star_resolves_columns_to_base_table():
    """CTE alias với A.* vẫn resolve về bảng gốc (STAR_ALIAS_FALLBACK)."""
    sql = """
        WITH tem_txn AS (
            SELECT A.*, MT.TRANSACTION_NO TMP_SEQ_NO
            FROM KM_JASPER.RPT_BSS081_NAPAS_TXN A
            JOIN KMDW.STA_RB_ACCT C ON A.ACCT_NO = C.ACCT_NO
            LEFT JOIN KMDW.FT_MEMO_TRAN_HIST MT ON A.TRANSACTION_NO = MT.RTH_SEQ_NO
        )
        SELECT T.ACCT_NO, T.ACCT_EXEC
        FROM tem_txn T
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert (
        "",
        "KM_JASPER",
        "RPT_BSS081_NAPAS_TXN",
        "ACCT_NO",
        "STAR_ALIAS_FALLBACK",
        "PROJECTION",
    ) in row_set
    assert (
        "",
        "KM_JASPER",
        "RPT_BSS081_NAPAS_TXN",
        "ACCT_EXEC",
        "STAR_ALIAS_FALLBACK",
        "PROJECTION",
    ) in row_set


def test_subquery_join_star_resolves_columns_in_clauses():
    """Subquery JOIN có SELECT * vẫn resolve cột ở PROJECTION/WHERE/JOIN_ON."""
    sql = """
        SELECT v.col1
        FROM t_main m
        JOIN (SELECT * FROM sch.sub_table) v ON m.id = v.id
        WHERE v.col1 IS NOT NULL
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "SCH", "SUB_TABLE", "COL1", "", "PROJECTION") in row_set
    assert ("", "SCH", "SUB_TABLE", "COL1", "", "WHERE") in row_set
    assert ("", "SCH", "SUB_TABLE", "ID", "", "JOIN_ON") in row_set


def test_order_by_alias_derived_resolves_to_dual():
    """ORDER BY alias của biểu thức thuần trả về DUAL với DERIVED_COLUMN."""
    sql = "SELECT 1 AS cnt FROM dual ORDER BY cnt"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "", "DUAL", "CNT", "DERIVED_COLUMN", "ORDER_BY") in row_set


def test_nested_select_output_resolves_column_from_cte_alias():
    """Truy vết cột ở select lồng trong CTE khi outer không select trực tiếp."""
    sql = """
        WITH tem_txn AS (
            SELECT x.SYM_RUN_DATE, x.MODULE
            FROM (
                SELECT a.SYM_RUN_DATE,
                       'MM' MODULE,
                       (a.MATURITY - a.VALUE_DATE) TERM
                FROM KMDW.FT_MM_BALANCE a
            ) x
        )
        SELECT T.SYM_RUN_DATE
        FROM tem_txn T
        WHERE T.TERM > 0
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "KMDW", "FT_MM_BALANCE", "TERM", "", "WHERE") in row_set


def test_cte_select_star_with_exists_resolves_base_table_column():
    """CTE SELECT * với EXISTS vẫn resolve cột về bảng gốc thay vì tên CTE."""
    sql = """
        WITH TMP_LOAN_T AS (
            SELECT *
            FROM KMDW.FT_LOAN_DD_BALANCE_STATIC S
            WHERE EXISTS (
                SELECT LOAN_NO
                FROM TMP_LOAN_INFOR I
                WHERE S.LOAN_NO = I.LOAN_NO
            )
        )
        SELECT S.LOAN_NO
        FROM TMP_LOAN_T S
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert (
        "",
        "KMDW",
        "FT_LOAN_DD_BALANCE_STATIC",
        "LOAN_NO",
        "SUBQUERY_STAR_FALLBACK",
        "PROJECTION",
    ) in row_set


def test_cte_alias_in_join_resolves_base_table_for_alias_column():
    """Alias CTE trong JOIN resolve về bảng gốc, không trả về tên CTE."""
    sql = """
        WITH TMP_LOAN_T AS (
            SELECT *
            FROM KMDW.FT_LOAN_DD_BALANCE_STATIC S
            WHERE S.SYM_RUN_DATE = KM_GET_RUN_DATE
        ),
        TMP_LOAN_T1 AS (
            SELECT *
            FROM KMDW.FT_LOAN_DD_BALANCE_STATIC S
            WHERE S.SYM_RUN_DATE = KM_GET_RUN_DATE-1
        )
        SELECT S1.LOAN_NO
        FROM TMP_LOAN_T S
        LEFT JOIN TMP_LOAN_T1 S1 ON S.LOAN_NO = S1.LOAN_NO
    """
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert (
        "",
        "KMDW",
        "FT_LOAN_DD_BALANCE_STATIC",
        "LOAN_NO",
        "CTE_SINGLE_TABLE_FALLBACK",
        "PROJECTION",
    ) in row_set


def test_placeholder_column_resolves_to_dual():
    """Cột có placeholder #...# phải resolve về DUAL."""
    sql = "SELECT #PDATE# AS RUN_DATE FROM DUAL"
    rows = _extract_rows(sql)
    row_set = _as_set(rows)
    assert ("", "", "DUAL", "#PDATE#", "PLACEHOLDER_COLUMN", "PROJECTION") in row_set
