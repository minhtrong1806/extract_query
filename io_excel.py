from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_excel_data(file_path: str | Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    """Đọc file Excel đầu vào và chuẩn hóa cột SELECT_STATEMENT_CLEANED."""
    data_path = Path(file_path)
    df = pd.read_excel(data_path, sheet_name=sheet_name)

    df["SELECT_STATEMENT_CLEANED"] = (
        df["SELECT_STATEMENT"]
        .astype(str)
        .str.replace(r"_x000D_", " ", regex=True, case=False)
        .str.replace("\r", " ")
        .str.replace("\n", " ")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    return df


def write_output_excel(df: pd.DataFrame, output_path: str | Path) -> None:
    """Ghi DataFrame kết quả ra file Excel."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output_path, index=False)
