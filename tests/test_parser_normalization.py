from __future__ import annotations

from parser import _mask_placeholders, _normalize_dynamic_sql, parse_select_statement


def test_parse_select_statement_handles_literal_hash_concat():
    sql = """
        WITH tmp AS (
            SELECT
                'CTR' || '#' || 'CTR3000001' || '#' || TO_CHAR(:IP_TO_DATE, 'yyyyMMdd') AS c02
            FROM dual
        )
        SELECT c02 FROM tmp
    """

    result = parse_select_statement(sql)

    assert result.error is None
    assert result.ast is not None
    assert result.placeholder_map == {}


def test_mask_placeholders_only_for_identifier_tokens():
    sql = "SELECT '#' AS delim, #IP_TO_DATE# AS p FROM dual"

    masked, placeholder_map = _mask_placeholders(sql)

    assert "'#'" in masked
    assert "__SQLGLOT_PH_0__" in masked
    assert placeholder_map == {"__SQLGLOT_PH_0__": "#IP_TO_DATE#"}


def test_normalize_dynamic_sql_keeps_escaped_single_quotes():
    sql = "SELECT '''' AS escaped_quote FROM dual"

    normalized = _normalize_dynamic_sql(sql)

    assert "''''" in normalized


def test_normalize_dynamic_sql_keeps_regular_literal_concat():
    sql = "SELECT 'CTR' || 'THANGWW' || 'CTR3000001' FROM dual"

    normalized = _normalize_dynamic_sql(sql)

    assert "'CTR' || 'THANGWW' || 'CTR3000001'" in normalized


def test_parse_select_statement_repairs_extra_close_before_union_in_cte():
    sql = """
        WITH tmp AS (
            SELECT 1 AS c
            FROM (SELECT 1 AS c FROM dual) )
        UNION ALL
        SELECT 2 AS c FROM dual
        )
        SELECT c FROM tmp
    """

    result = parse_select_statement(sql)

    assert result.error is None
    assert result.ast is not None
