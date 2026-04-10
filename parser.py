from __future__ import annotations

import re
from typing import Dict, Tuple

from sqlglot import parse_one
from sqlglot.errors import ParseError, TokenError

from models import ParseResult

PLACEHOLDER_PATTERN = re.compile(r"#([^#]+)#")
DYNAMIC_CONCAT_PATTERN = re.compile(
    r"'{1,3}\s*\|\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\|\s*'{1,3}",
    re.IGNORECASE,
)
INLINE_SELECT_INJECTION_PATTERN = re.compile(
    r",\s*SELECT\s+.*?\bWHERE\s+1\s*=\s*1'\s*;",
    re.IGNORECASE | re.DOTALL,
)
LITERAL_CONCAT_PATTERN = re.compile(
    r"'{1,3}\s*\|\|\s*'([^']*)'\s*\|\|\s*'{1,3}",
    re.IGNORECASE,
)
QUOTED_CHUNK_CONCAT_PATTERN = re.compile(r"'\s*\|\|\s*'", re.IGNORECASE)
GENERIC_CONCAT_VAR_PATTERN = re.compile(
    r"\|\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\|",
    re.IGNORECASE,
)


def _mask_placeholders(statement: str) -> Tuple[str, Dict[str, str]]:
    placeholder_map: Dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        token = f"__SQLGLOT_PH_{len(placeholder_map)}__"
        placeholder_map[token] = match.group(0)
        return token

    return PLACEHOLDER_PATTERN.sub(_replace, statement), placeholder_map


def _normalize_dynamic_sql(statement: str) -> str:
    """Chuẩn hóa SQL động dạng ghép chuỗi PL/SQL thành SQL parse được."""
    if not statement:
        return statement

    normalized = DYNAMIC_CONCAT_PATTERN.sub(lambda m: f"#{m.group(1)}#", statement)
    normalized = GENERIC_CONCAT_VAR_PATTERN.sub(lambda m: f"#{m.group(1)}#", normalized)
    normalized = LITERAL_CONCAT_PATTERN.sub(lambda m: f"'{m.group(1)}'", normalized)
    normalized = QUOTED_CHUNK_CONCAT_PATTERN.sub("", normalized)
    normalized = INLINE_SELECT_INJECTION_PATTERN.sub("", normalized)
    normalized = re.sub(r",\s*SELECT\s+", ", ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(#[A-Za-z_][A-Za-z0-9_]*#)'(\s*,)", r"\1\2", normalized)
    normalized = re.sub(r"(#[A-Za-z_][A-Za-z0-9_]*#)'(\s+FROM\b)", r"\1\2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"(?<=[\s\)])'(?=(WHEN|FROM|WHERE|ORDER\s+BY|GROUP\s+BY|AND|OR|CASE|ELSE|END)\b)",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"PARTITION\s*\(\s*P\s*#([^#]+)#\s*\)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"PARTITION\s*\([^\)]*\)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace("''", "'")
    if normalized.count("'") % 2 == 1:
        normalized = re.sub(r"'\s*$", "", normalized)
    normalized = re.sub(r"'\s*;\s*$", ";", normalized)
    return normalized


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
        try:
            normalized_statement = _normalize_dynamic_sql(statement)
            masked_statement, placeholder_map = _mask_placeholders(normalized_statement)
            ast = parse_one(masked_statement, read="oracle")
            return ParseResult(ast=ast, placeholder_map=placeholder_map, error=None)
        except (ParseError, TokenError, ValueError) as normalized_exc:
            return ParseResult(ast=None, placeholder_map={}, error=f"{exc} | normalized: {normalized_exc}")
