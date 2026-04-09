from __future__ import annotations

from pathlib import Path
import re

import pandas as pd


COMMENT_KEYWORDS = re.compile(
    r"\b(SELECT|FROM|WHERE|AND|OR|JOIN|LEFT|RIGHT|FULL|INNER|OUTER|CROSS|"
    r"GROUP|HAVING|ORDER|UNION|INTERSECT|EXCEPT|QUALIFY|WINDOW)\b",
    re.IGNORECASE,
)


def _find_comment_end(sql: str, start: int) -> int:
    candidates: list[int] = []
    for token in ("\n", "\r", ",", ")", ";"):
        idx = sql.find(token, start)
        if idx != -1:
            candidates.append(idx)
    match = COMMENT_KEYWORDS.search(sql, start)
    if match:
        candidates.append(match.start())
    return min(candidates) if candidates else len(sql)


def _convert_inline_comments_to_block(sql: str) -> str:
    if not sql or "--" not in sql:
        return sql
    result: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False

    while i < n:
        ch = sql[i]

        if ch == "'" and not in_double:
            result.append(ch)
            if in_single:
                if i + 1 < n and sql[i + 1] == "'":
                    result.append("'")
                    i += 2
                    continue
                in_single = False
                i += 1
                continue
            in_single = True
            i += 1
            continue

        if ch == '"' and not in_single:
            result.append(ch)
            in_double = not in_double
            i += 1
            continue

        if not in_single and not in_double and ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            end_idx = _find_comment_end(sql, i)
            comment_text = sql[i:end_idx].strip()
            result.append("/*")
            result.append(comment_text)
            result.append("*/")
            i = end_idx
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def read_excel_data(file_path: str | Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """Đọc file Excel đầu vào và chuẩn hóa cột SELECT_STATEMENT_CLEANED."""
    data_path = Path(file_path)
    df = pd.read_excel(data_path, sheet_name=sheet_name)

    df["SELECT_STATEMENT_CLEANED"] = (
        df["SELECT_STATEMENT"]
        .astype(str)
        .str.replace(r"_x000D_", "\n", regex=True, case=False)
        .str.replace("\r\n", "\n")
        .str.replace("\r", "\n")
        .str.replace(r"[\t ]+", " ", regex=True)
        .str.strip()
    )
    df["SELECT_STATEMENT_CLEANED"] = df["SELECT_STATEMENT_CLEANED"].apply(
        _convert_inline_comments_to_block
    )
    return df


def write_output_excel(df: pd.DataFrame, output_path: str | Path) -> None:
    """Ghi DataFrame kết quả ra file Excel."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
