from __future__ import annotations

import main


def test_where_for_current_column_keeps_only_matching_column_predicates():
    where_text = """
        FT_CAMP_TRAN.SYM_RUN_DATE = TRUNC(SYSDATE) - 1
        AND (FT_CAMP_TRAN.IS_PAID <> 'S' OR FT_CAMP_TRAN.IS_PAID IS NULL)
        AND FT_CAMP_TRAN.CAMPAIGN_ID = 'TUITION'
    """

    result = main._where_for_current_column(where_text, "FT_CAMP_TRAN", "SYM_RUN_DATE").upper()

    assert "FT_CAMP_TRAN.SYM_RUN_DATE = TRUNC(SYSDATE" in result and "- 1" in result
    assert "IS_PAID" not in result
    assert "CAMPAIGN_ID" not in result


def test_where_for_current_column_keeps_join_predicate_for_matching_column():
    where_text = (
        "STA_FM_GL_MAST.GL_CODE = FT_GL_BALANCE_SBV.GL_CODE "
        "AND FT_GL_BALANCE_SBV.BRANCH_NO = '001'"
    )

    result = main._where_for_current_column(where_text, "FT_GL_BALANCE_SBV", "GL_CODE").upper()

    assert "STA_FM_GL_MAST.GL_CODE = FT_GL_BALANCE_SBV.GL_CODE" in result
    assert "BRANCH_NO" not in result


def test_where_for_current_column_supports_unqualified_predicate():
    where_text = "SYM_RUN_DATE = :IP_DATE AND CAMPAIGN_ID = 'TUITION'"

    result = main._where_for_current_column(where_text, "FT_CAMP_TRAN", "CAMPAIGN_ID").upper()

    assert "CAMPAIGN_ID = 'TUITION'" in result
    assert "SYM_RUN_DATE" not in result


def test_where_for_current_column_handles_quoted_identifiers():
    where_text = '"MY_TABLE"."MY_COL" = 1 AND "MY_TABLE"."OTHER_COL" = 2'

    result = main._where_for_current_column(where_text, "MY_TABLE", "MY_COL").upper()

    assert '"MY_TABLE"."MY_COL" = 1' in result
    assert "OTHER_COL" not in result


def test_where_for_current_column_matches_table_with_dblink_suffix():
    where_text = '"CL_ACCRUALS_TBL@KMAPP_TO_EOC".DD_KEY = "CL_DRAWDOWN_TBL@KMAPP_TO_EOC".DD_KEY'

    result = main._where_for_current_column(where_text, "CL_ACCRUALS_TBL", "DD_KEY").upper()

    assert '"CL_ACCRUALS_TBL@KMAPP_TO_EOC".DD_KEY = "CL_DRAWDOWN_TBL@KMAPP_TO_EOC".DD_KEY' in result


def test_predicate_matches_column_uses_regex_fallback_when_parse_unavailable(monkeypatch):
    monkeypatch.setattr(main, "_extract_column_refs_from_predicate", lambda _predicate: tuple())

    assert main._predicate_matches_column("A.COL1 = 1", "A", "COL1") is True
    assert main._predicate_matches_column("B.COL1 = 1", "A", "COL1") is False
