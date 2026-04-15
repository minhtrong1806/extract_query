from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import List

import pandas as pd
from sqlglot import expressions as exp, parse_one

from extractor import extract_schema_table_column_rows
from parser import parse_select_statement


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ILLEGAL_XLSX_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean_sql(sql_text: str) -> str:
    return (
        str(sql_text or "")
        .replace("_x000D_", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )


def _sanitize_excel_text(value: str) -> str:
    text = str(value or "")
    text = ANSI_ESCAPE_RE.sub("", text)
    text = ILLEGAL_XLSX_RE.sub("", text)
    text = "".join(
        ch
        for ch in text
        if ch in "\t\n\r" or unicodedata.category(ch) != "Cc"
    )
    return text


def _split_table_dblink(table_name: str) -> tuple[str, str]:
    table = (table_name or "").strip()
    if not table:
        return "", ""
    if "@" not in table:
        return table, ""
    base_table, dblink = table.split("@", 1)
    dblink = dblink.strip()
    return base_table.strip(), (f"@{dblink}" if dblink else "")


def _split_and_predicates(expression: exp.Expression) -> List[exp.Expression]:
    if isinstance(expression, exp.And):
        return _split_and_predicates(expression.this) + _split_and_predicates(expression.expression)
    return [expression]


def _extract_predicates_from_text(where_text: str) -> List[str]:
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
        return [cleaned]


def _merge_unique_where(values) -> str:
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


def _error_row(row_id: int, report_id: str, system_name: str, report_name: str, log_message: str) -> dict[str, str | int]:
    return {
        "_INPUT_ROW": row_id,
        "REPORT_ID": report_id,
        "SYSTEM": system_name,
        "REPORT_NAME": report_name,
        "SCHEMA": "",
        "TABLE": "",
        "DBLINK": "",
        "COLUMN": "",
        "WHERE_CONDITION": "",
        "logs": _sanitize_excel_text(log_message),
    }


def _extract_one_input_row(row_id: int, row: pd.Series) -> pd.DataFrame:
    report_id = str(row.get("REPORT_ID", "") or "").strip()
    report_name = str(row.get("REPORT_NAME", "") or "").strip()
    system_name = str(row.get("SYSTEM", "") or "").strip() or "KMPV"

    sql_raw = row.get("report_script", "")
    if pd.isna(sql_raw) or not str(sql_raw).strip():
        return pd.DataFrame([_error_row(row_id, report_id, system_name, report_name, "EMPTY_SQL")])

    sql_text = _clean_sql(str(sql_raw))
    parse_result = parse_select_statement(sql_text)
    if parse_result.ast is None:
        error_message = str(parse_result.error or "PARSE_FAILED").replace("\n", " ").strip()
        return pd.DataFrame([_error_row(row_id, report_id, system_name, report_name, f"PARSE_ERROR: {error_message}")])

    try:
        extracted_rows = extract_schema_table_column_rows(
            parse_result.ast,
            parse_result.placeholder_map,
            sql_text=sql_text,
        )
    except Exception as exc:  # pragma: no cover - bảo vệ runtime
        return pd.DataFrame(
            [
                _error_row(
                    row_id,
                    report_id,
                    system_name,
                    report_name,
                    f"EXTRACT_ERROR: {type(exc).__name__}: {exc}",
                )
            ]
        )

    output_rows: list[dict[str, str | int]] = []
    for extracted in extracted_rows:
        schema_name = str(extracted.get("SCHEMA", "") or "").strip()
        table_name = str(extracted.get("TABLE", "") or "").strip()
        if "." in table_name:
            table_name = table_name.split(".")[-1].strip()
        table_name, dblink = _split_table_dblink(table_name)

        column_name = str(extracted.get("COLUMN", "") or "").strip()
        if column_name.upper().startswith("V_"):
            continue

        where_condition = _sanitize_excel_text(str(extracted.get("WHERE_CONDITION", "") or "").strip())
        reason = str(extracted.get("REASON", "") or "").strip()
        log_message = _sanitize_excel_text(reason if reason else "OK")

        output_rows.append(
            {
                "_INPUT_ROW": row_id,
                "REPORT_ID": report_id,
                "SYSTEM": system_name,
                "REPORT_NAME": report_name,
                "SCHEMA": schema_name,
                "TABLE": table_name,
                "DBLINK": dblink,
                "COLUMN": column_name,
                "WHERE_CONDITION": where_condition,
                "logs": log_message,
            }
        )

    if not output_rows:
        return pd.DataFrame([_error_row(row_id, report_id, system_name, report_name, "NO_EXTRACTED_ROWS")])

    output_df = pd.DataFrame(output_rows)

    # Dedup theo từng dòng input để tránh dedup chéo mã báo cáo.
    where_df = output_df.loc[
        output_df["WHERE_CONDITION"].fillna("").astype(str).str.strip().ne(""),
        ["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK", "WHERE_CONDITION"],
    ].copy()
    where_df["WHERE_CONDITION"] = where_df["WHERE_CONDITION"].astype(str).str.strip()
    where_df = where_df.drop_duplicates(
        subset=["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK", "WHERE_CONDITION"]
    )
    where_map = (
        where_df
        .groupby(["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK"], dropna=False, sort=False)["WHERE_CONDITION"]
        .agg(_merge_unique_where)
        .reset_index()
        .rename(columns={"WHERE_CONDITION": "_WHERE_CONDITION_TABLE"})
    )

    output_df["_has_where"] = output_df["WHERE_CONDITION"].fillna("").str.len().gt(0).astype(int)
    output_df["_where_len"] = output_df["WHERE_CONDITION"].fillna("").str.len()
    output_df["_has_log"] = output_df["logs"].fillna("").str.upper().ne("OK").astype(int)
    output_df = output_df.sort_values(
        by=["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK", "COLUMN", "_has_where", "_has_log", "_where_len"],
        ascending=[True, True, True, True, True, True, False, False, False],
        kind="mergesort",
        na_position="last",
    )
    output_df = output_df.drop_duplicates(
        subset=["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK", "COLUMN"]
    ).reset_index(drop=True)

    if not where_map.empty:
        output_df = output_df.merge(
            where_map,
            on=["_INPUT_ROW", "REPORT_ID", "SCHEMA", "TABLE", "DBLINK"],
            how="left",
        )
        output_df["WHERE_CONDITION"] = output_df["_WHERE_CONDITION_TABLE"].where(
            output_df["_WHERE_CONDITION_TABLE"].fillna("").str.strip().ne(""),
            output_df["WHERE_CONDITION"],
        )

    return output_df.drop(columns=["_WHERE_CONDITION_TABLE", "_has_where", "_where_len", "_has_log"], errors="ignore")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    for logger_name in ("extractor.extractors", "pipeline", "parser"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    base_dir = Path(__file__).resolve().parent
    input_path = base_dir / "data" / "KMPV_SCRIPT_REPORT_10APR2026_V1.0_merged.xlsx"
    output_path = base_dir / "output" / "KMPV_SCRIPT_REPORT_10APR2026_V1.0_merged_SCHEMA_TABLE_COLUMN.xlsx"

    source_df = pd.read_excel(input_path, sheet_name="Sheet1")

    select_star_clean_sql = source_df.get("report_script", pd.Series(dtype=object)).astype(str).map(_clean_sql)
    select_star_mask = select_star_clean_sql.map(_has_select_star)
    if bool(select_star_mask.any()):
        star_df = source_df.loc[select_star_mask, [col for col in ["REPORT_ID", "SYSTEM", "REPORT_NAME"] if col in source_df.columns]]
        logging.warning(
            "Phat hien %s report co SELECT * / alias.* trong KMPV input",
            int(select_star_mask.sum()),
        )
        for row_idx, row in star_df.head(20).iterrows():
            logging.warning(
                "[SELECT_STAR] row=%s REPORT_ID=%s SYSTEM=%s REPORT_NAME=%s",
                row_idx,
                str(row.get("REPORT_ID", "") or "").strip(),
                str(row.get("SYSTEM", "") or "").strip(),
                str(row.get("REPORT_NAME", "") or "").strip(),
            )
        if len(star_df) > 20:
            logging.warning("[SELECT_STAR] ... va %s report khac", len(star_df) - 20)

    chunks: list[pd.DataFrame] = []

    for row_id, row in source_df.iterrows():
        chunks.append(_extract_one_input_row(row_id, row))

    result_df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if result_df.empty:
        result_df = pd.DataFrame(
            columns=[
                "REPORT_ID",
                "SYSTEM",
                "REPORT_NAME",
                "Tên schema",
                "Tên Bảng",
                "Tên DBlink",
                "Tên Cột",
                "Điều kiện lấy",
                "logs",
            ]
        )
    else:
        result_df = result_df.rename(
            columns={
                "SCHEMA": "Tên schema",
                "TABLE": "Tên Bảng",
                "DBLINK": "Tên DBlink",
                "COLUMN": "Tên Cột",
                "WHERE_CONDITION": "Điều kiện lấy",
            }
        )
        result_df = result_df[
            [
                "REPORT_ID",
                "SYSTEM",
                "REPORT_NAME",
                "Tên schema",
                "Tên Bảng",
                "Tên DBlink",
                "Tên Cột",
                "Điều kiện lấy",
                "logs",
            ]
        ]

    text_columns = [
        "REPORT_ID",
        "SYSTEM",
        "REPORT_NAME",
        "Tên schema",
        "Tên Bảng",
        "Tên DBlink",
        "Tên Cột",
        "Điều kiện lấy",
        "logs",
    ]
    for col in text_columns:
        if col in result_df.columns:
            result_df[col] = result_df[col].fillna("").astype(str).map(_sanitize_excel_text)

    for col in ("Tên schema", "Tên Bảng", "Tên Cột", "Điều kiện lấy"):
        if col in result_df.columns:
            result_df[col] = result_df[col].str.upper()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_df.to_excel(output_path, index=False)

    parse_error_count = result_df["logs"].astype(str).str.startswith("PARSE_ERROR").sum() if "logs" in result_df.columns else 0
    print(f"DONE - rows={len(result_df)} parse_errors={parse_error_count} output={output_path}")


if __name__ == "__main__":
    main()
