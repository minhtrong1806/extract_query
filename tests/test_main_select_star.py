from __future__ import annotations

import main
from models import ParseResult


def test_has_select_star_detects_plain_star_with_bind_variable():
    sql = """
        SELECT *
        FROM TOI.TOI_MANUAL_WB_ALL D
        WHERE D.SYM_RUN_DATE = :IP_TO_DATE
    """
    assert main._has_select_star(sql) is True


def test_has_select_star_fallback_detects_star_when_parse_fail(monkeypatch):
    sql = """
        SELECT DISTINCT D.*
        FROM TOI.TOI_MANUAL_WB_ALL D
        WHERE D.SYM_RUN_DATE = :IP_TO_DATE
    """

    monkeypatch.setattr(
        main,
        "parse_select_statement",
        lambda _sql: ParseResult(ast=None, placeholder_map={}, error="forced parse fail"),
    )

    assert main._has_select_star(sql) is True


def test_has_select_star_does_not_flag_arithmetic_expression(monkeypatch):
    sql = "SELECT price * qty AS total FROM sales"

    monkeypatch.setattr(
        main,
        "parse_select_statement",
        lambda _sql: ParseResult(ast=None, placeholder_map={}, error="forced parse fail"),
    )

    assert main._has_select_star(sql) is False
