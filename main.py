from pathlib import Path
import logging

from io_excel import read_excel_data, write_output_excel
from pipeline import build_output_dataframe
    
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    data_path = Path(__file__).resolve().parent / "data" / "ETL_SCRIPT_KM_ETL.xlsx"
    output_path = Path(__file__).resolve().parent / "output" / "ETL_SCRIPT_KM_ETL_SCHEMA_TABLE_COLUMN.xlsx"

    df = read_excel_data(data_path)
    output_df = build_output_dataframe(df)
    data_mask = (
        output_df["SCHEMA"].fillna("").ne("")
        | output_df["TABLE"].fillna("").ne("")
        | output_df["COLUMN"].fillna("").ne("")
    )
    output_df = output_df.loc[data_mask]
    output_df = output_df.drop_duplicates(subset=["SCHEMA", "TABLE", "COLUMN"]).reset_index(drop=True)
    write_output_excel(output_df, output_path)

if __name__ == "__main__":
    main()
