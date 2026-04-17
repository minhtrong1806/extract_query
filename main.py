from pathlib import Path
import logging
import re
from functools import lru_cache
from typing import List

from sqlglot import expressions as exp, parse_one

from io_excel import read_excel_data, write_output_excel
from parser import parse_select_statement
from pipeline import build_output_dataframe


def _split_and_predicates(expression: exp.Expression) -> List[exp.Expression]:
    if isinstance(expression, exp.And):
        return _split_and_predicates(expression.this) + _split_and_predicates(expression.expression)
    return [expression]


def _extract_predicates_from_text(where_text: str) -> List[str]:
    """Tách predicate theo AND ở top-level bằng SQL parser (an toàn cho BETWEEN ... AND ...)."""
    cleaned = re.sub(r"(?i)^WHERE\s+", "", (where_text or "").strip()).strip()
    cleaned = re.sub(r"(?i)^AND\s+", "", cleaned).strip()
    if not cleaned:
        return []

    try:
        stmt = parse_one(f"SELECT 1 FROM DUAL WHERE {cleaned}", read="oracle")
        where = stmt.args.get("where")
        if not isinstance(where, exp.Where) or where.this is None:
            return [cleaned]
        predicates = _split_and_predicates(where.this)
        result = [item.sql(dialect="oracle").strip() for item in predicates if isinstance(item, exp.Expression)]
        return [item for item in result if item]
    except Exception:
        # Fallback: giữ nguyên nguyên cụm để tránh split sai cú pháp.
        return [cleaned]


def _merge_unique_where(values) -> str:
    """Gộp WHERE_CONDITION theo từng predicate, loại trùng theo bảng."""

    def _normalize_key(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(?i)^AND\s+", "", text).strip()
        return text.upper()

    seen: set[str] = set()
    predicates: list[str] = []

    for value in values:
        raw = str(value or "")
        if not raw.strip():
            continue

        # Tách theo ';' trước (thường ngăn cách từng nhóm điều kiện),
        # sau đó tách AND top-level bằng parser.
        chunks = [chunk.strip() for chunk in re.split(r"[;]+", raw) if chunk.strip()]
        for chunk in chunks:
            for predicate in _extract_predicates_from_text(chunk):
                key = _normalize_key(predicate)
                if not key or key in seen:
                    continue
                seen.add(key)
                predicates.append(predicate)

    if not predicates:
        return ""
    if len(predicates) == 1:
        return predicates[0]
    return predicates[0] + "\nAND " + "\nAND ".join(predicates[1:])


def _normalize_identifier(name: str) -> str:
    """Chuẩn hóa định danh SQL (table/column) để so khớp ổn định."""
    text = str(name or "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1].strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1].replace('""', '"')
    return text.upper()


@lru_cache(maxsize=8192)
def _extract_column_refs_from_predicate(predicate_sql: str) -> tuple[tuple[str, str], ...]:
    """Trích xuất danh sách (TABLE, COLUMN) xuất hiện trong 1 predicate."""
    cleaned = str(predicate_sql or "").strip()
    if not cleaned:
        return tuple()

    try:
        stmt = parse_one(f"SELECT 1 FROM DUAL WHERE {cleaned}", read="oracle")
        where = stmt.args.get("where")
        if not isinstance(where, exp.Where) or where.this is None:
            return tuple()

        refs: list[tuple[str, str]] = []
        for column in where.this.find_all(exp.Column):
            if bool(getattr(column, "is_star", False)):
                continue
            refs.append(
                (
                    _normalize_identifier(column.table or ""),
                    _normalize_identifier(column.name or ""),
                )
            )
        return tuple(refs)
    except Exception:
        return tuple()


def _fallback_predicate_matches_column(
    predicate_sql: str,
    table_name: str,
    column_name: str,
) -> bool:
    """Fallback regex khi parser không parse được predicate."""
    target_table = _normalize_identifier(table_name)
    target_column = _normalize_identifier(column_name)
    if not target_column:
        return False

    normalized_predicate = re.sub(
        r'"([^"]+)"',
        lambda match: match.group(1).replace('""', '"'),
        str(predicate_sql or ""),
    ).upper()

    identifier_char_class = r"A-Z0-9_$#"
    column_token = re.escape(target_column)

    if target_table:
        table_token = re.escape(target_table)
        if re.search(
            rf"(?<![{identifier_char_class}]){table_token}\s*\.\s*{column_token}(?![{identifier_char_class}])",
            normalized_predicate,
        ):
            return True
        return bool(
            re.search(
                rf"(?<![{identifier_char_class}\.]){column_token}(?![{identifier_char_class}])",
                normalized_predicate,
            )
        )

    return bool(
        re.search(
            rf"(?<![{identifier_char_class}])(?:[A-Z0-9_$#]+\s*\.\s*)?{column_token}(?![{identifier_char_class}])",
            normalized_predicate,
        )
    )


def _predicate_matches_column(
    predicate_sql: str,
    table_name: str,
    column_name: str,
) -> bool:
    """Kiểm tra predicate có áp dụng cho (TABLE, COLUMN) hiện tại hay không."""
    target_table = _normalize_identifier(table_name)
    target_column = _normalize_identifier(column_name)
    if not target_column:
        return False

    refs = _extract_column_refs_from_predicate(str(predicate_sql or ""))
    if refs:
        for ref_table, ref_column in refs:
            if ref_column != target_column:
                continue
            # ref_table rỗng nghĩa là cột unqualified -> cho phép map theo cột hiện tại.
            if not target_table or not ref_table or ref_table == target_table:
                return True
        return False

    return _fallback_predicate_matches_column(predicate_sql, table_name, column_name)


def _where_for_current_column(
    where_text: str,
    table_name: str,
    column_name: str,
) -> str:
    """Lấy WHERE_CONDITION chỉ gồm các predicate liên quan trực tiếp tới cột hiện tại."""
    raw = str(where_text or "").strip()
    if not raw or not _normalize_identifier(column_name):
        return ""

    matched_predicates: list[str] = []
    chunks = [chunk.strip() for chunk in re.split(r"[;]+", raw) if chunk.strip()]
    for chunk in chunks:
        predicates = _extract_predicates_from_text(chunk)
        for predicate in predicates:
            if _predicate_matches_column(predicate, table_name, column_name):
                matched_predicates.append(predicate)

    return _merge_unique_where(matched_predicates)


def _has_select_star(sql_text: str) -> bool:
    """Kiểm tra SELECT list có chứa * hay alias.* để cảnh báo tự rà soát."""
    text = str(sql_text or "").strip()
    if not text:
        return False

    parse_result = parse_select_statement(text)
    if parse_result.ast is not None:
        for select in parse_result.ast.find_all(exp.Select):
            for projection in select.expressions or []:
                if isinstance(projection, exp.Star):
                    return True
                if isinstance(projection, exp.Column) and bool(getattr(projection, "is_star", False)):
                    return True
                if any(True for _ in projection.find_all(exp.Star)):
                    return True

    # Fallback khi parse fail: tách SELECT list bằng regex và dò token * / alias.*
    # (không dùng \b quanh * vì * không phải word-char).
    scrubbed_text = re.sub(r"'(?:''|[^'])*'", "''", text)
    scrubbed_text = re.sub(r'"(?:""|[^"])*"', '""', scrubbed_text)

    for match in re.finditer(r"(?is)\bSELECT\b(?P<select_list>[\s\S]*?)\bFROM\b", scrubbed_text):
        select_list = match.group("select_list")
        select_list = re.sub(r"(?is)^\s*(ALL|DISTINCT|UNIQUE)\s+", "", select_list)
        if re.search(
            r"(?is)(^|,)\s*(?:(?:\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_$#]*)\s*\.\s*)?\*\s*(?=,|$)",
            select_list,
        ):
            return True

    return False
    
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    data_path = Path(__file__).resolve().parent / "data" / "ETL_SCRIPT_KM_ETL.xlsx"
    output_path = Path(__file__).resolve().parent / "output" / "ETL_SCRIPT_KM_ETL_SCHEMA_TABLE_COLUMN.xlsx"
    select_star_alert_path = Path(__file__).resolve().parent / "output" / "ETL_SCRIPT_KM_ETL_SELECT_STAR_ALERT.xlsx"

    df = read_excel_data(data_path)

    select_star_mask = df["SELECT_STATEMENT_CLEANED"].astype(str).apply(_has_select_star)
    if select_star_mask.any():
        star_df = df.loc[
            select_star_mask,
            [col for col in ["JOB_NAME", "READ_MODE", "SELECT_STATEMENT", "SELECT_STATEMENT_CLEANED"] if col in df.columns],
        ].copy()
        star_df.insert(0, "ROW_INDEX", star_df.index)
        write_output_excel(star_df, select_star_alert_path)
        logging.warning(
            "Phat hien %s dong co SELECT * / alias.*. Da ghi danh sach can tu check tai: %s",
            len(star_df),
            select_star_alert_path,
        )

    output_df = build_output_dataframe(df)
    data_mask = (
        output_df["SCHEMA"].fillna("").ne("")
        | output_df["TABLE"].fillna("").ne("")
        | output_df["DBLINK"].fillna("").ne("")
        | output_df["COLUMN"].fillna("").ne("")
    )
    output_df = output_df.loc[data_mask]

    # Chỉ giữ điều kiện tương ứng với cột hiện tại của từng dòng.
    output_df["_WHERE_CONDITION_COLUMN"] = output_df.apply(
        lambda row: _where_for_current_column(
            row.get("WHERE_CONDITION", ""),
            row.get("TABLE", ""),
            row.get("COLUMN", ""),
        ),
        axis=1,
    )

    # Gom điều kiện theo đúng khóa (SCHEMA, TABLE, DBLINK, COLUMN)
    # để không mất predicate khi drop_duplicates.
    column_where_df = output_df.loc[
        output_df["_WHERE_CONDITION_COLUMN"].fillna("").astype(str).str.strip().ne(""),
        ["SCHEMA", "TABLE", "DBLINK", "COLUMN", "_WHERE_CONDITION_COLUMN"],
    ].copy()
    column_where_df["_WHERE_CONDITION_COLUMN"] = (
        column_where_df["_WHERE_CONDITION_COLUMN"].astype(str).str.strip()
    )
    column_where_df = column_where_df.drop_duplicates(
        subset=["SCHEMA", "TABLE", "DBLINK", "COLUMN", "_WHERE_CONDITION_COLUMN"]
    )
    column_where_map = (
        column_where_df
        .groupby(["SCHEMA", "TABLE", "DBLINK", "COLUMN"], dropna=False, sort=False)["_WHERE_CONDITION_COLUMN"]
        .agg(_merge_unique_where)
        .reset_index()
        .rename(columns={"_WHERE_CONDITION_COLUMN": "_WHERE_CONDITION_COLUMN_MERGED"})
    )

    output_df["_has_where"] = output_df["_WHERE_CONDITION_COLUMN"].fillna("").str.len().gt(0).astype(int)
    output_df["_where_len"] = output_df["_WHERE_CONDITION_COLUMN"].fillna("").str.len()
    output_df = output_df.sort_values(
        by=["SCHEMA", "TABLE", "DBLINK", "COLUMN", "_has_where", "_where_len"],
        ascending=[True, True, True, True, False, False],
        kind="mergesort",
        na_position="last",
    )
    output_df = output_df.drop_duplicates(subset=["SCHEMA", "TABLE", "DBLINK", "COLUMN"]).reset_index(drop=True)

    if not column_where_map.empty:
        output_df = output_df.merge(
            column_where_map,
            on=["SCHEMA", "TABLE", "DBLINK", "COLUMN"],
            how="left",
        )
        output_df["WHERE_CONDITION"] = output_df["_WHERE_CONDITION_COLUMN_MERGED"].where(
            output_df["_WHERE_CONDITION_COLUMN_MERGED"].fillna("").str.strip().ne(""),
            output_df["_WHERE_CONDITION_COLUMN"],
        )
    else:
        output_df["WHERE_CONDITION"] = output_df["_WHERE_CONDITION_COLUMN"]

    output_df = output_df.drop(columns=["_has_where", "_where_len"], errors="ignore")
    output_df = output_df.drop(
        columns=["_WHERE_CONDITION_COLUMN", "_WHERE_CONDITION_COLUMN_MERGED"],
        errors="ignore",
    )
    write_output_excel(output_df, output_path)

if __name__ == "__main__":
    main()
