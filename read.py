from __future__ import annotations

from pathlib import Path

import pandas as pd


def read(file_path: str | Path) -> pd.DataFrame:

    data_path = Path(file_path)
    df = pd.read_excel(data_path, sheet_name="full_ETL_jobs")

    # df = df.loc[df["READ_MODE"] == "Select"]

    df["SELECT_STATEMENT_CLEANED"] = (
        df["SELECT_STATEMENT"]
        .astype(str)
        .str.replace(r"_x000D_", " ", regex=True, case=False)
        .str.replace("#", "")
        .str.replace("\r", " ")
        .str.replace("\n", " ")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    return df
