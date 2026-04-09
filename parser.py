from __future__ import annotations

import re
from typing import Dict, Tuple

from sqlglot import parse_one
from sqlglot.errors import ParseError, TokenError

from models import ParseResult

PLACEHOLDER_PATTERN = re.compile(r"#([^#]+)#")


def _mask_placeholders(statement: str) -> Tuple[str, Dict[str, str]]:
    placeholder_map: Dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__SQLGLOT_PH_{len(placeholder_map)}__"
        placeholder_map[token] = match.group(0)
        return token

    return PLACEHOLDER_PATTERN.sub(_replace, statement), placeholder_map


def parse_select_statement(statement: object) -> ParseResult:
    """Parse câu lệnh SQL SELECT và trả về ParseResult.

    Tham số:
        statement: Giá trị câu lệnh thô (kỳ vọng là chuỗi).

    Trả về:
        ParseResult gồm AST, placeholder_map và lỗi parse (nếu có).
    """
    if not isinstance(statement, str) or not statement.strip():
        return ParseResult(ast=None, placeholder_map={}, error=None)

    try:
        masked_statement, placeholder_map = _mask_placeholders(statement)
        ast = parse_one(masked_statement, read="oracle")
        return ParseResult(ast=ast, placeholder_map=placeholder_map, error=None)
    except (ParseError, TokenError, ValueError) as exc:
        return ParseResult(ast=None, placeholder_map={}, error=str(exc))
