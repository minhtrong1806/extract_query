from __future__ import annotations

import kmpv


def test_kmpv_has_select_star_detects_plain_star_with_bind_variable():
    sql = """
        SELECT *
        FROM TOI.TOI_MANUAL_WB_ALL D
        WHERE D.SYM_RUN_DATE = :IP_TO_DATE
    """
    assert kmpv._has_select_star(sql) is True


def test_kmpv_has_select_star_does_not_flag_arithmetic_expression():
    sql = "SELECT price * qty AS total FROM sales"
    assert kmpv._has_select_star(sql) is False
