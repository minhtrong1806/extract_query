from pathlib import Path

from sqlglot import parse_one
from sqlglot.errors import ParseError, TokenError

from read import read
from process import extract_column_list, extract_table_list, extract_where_list

def parse_select_statement(statement: object):
    if not isinstance(statement, str) or not statement.strip():
        return None, None
    try:
        return parse_one(statement, read="oracle"), None
    except (ParseError, TokenError, ValueError) as exc:
        return None, str(exc)

def extract_from_ast(ast_result, extractor):
        ast, _err = ast_result
        if ast is None:
            return []
        return extractor(ast)
    
def main() -> None:
    
    data_path = Path(__file__).resolve().parent / "data" / "KM_TO_OT_SYS_20260301_V1-2.xlsx"
    output_path = Path(__file__).resolve().parent / "output" / "output.xlsx"
    
    #read data
    df = read(data_path)
    # df = df.head(10)
    
    # Parse SELECT_STATEMENT_CLEANED and store AST
    df["AST"] = df["SELECT_STATEMENT_CLEANED"].apply(parse_select_statement)

    # Create a mask for rows where READ_MODE is "Select"
    select_mask = df["READ_MODE"].eq("Select")

    # Initialize null values for table, column, and where clauses
    df["TABLE"] = ["" for _ in range(len(df))]
    df["COLUMN"] = ["" for _ in range(len(df))]
    df["WHERE"] = ["" for _ in range(len(df))]

    df.loc[select_mask, "TABLE"] = df.loc[select_mask, "AST"].apply(
        lambda ast_result: extract_from_ast(ast_result, extract_table_list)
    )
    df.loc[select_mask, "COLUMN"] = df.loc[select_mask, "AST"].apply(
        lambda ast_result: extract_from_ast(ast_result, extract_column_list)
    )
    df.loc[select_mask, "WHERE"] = df.loc[select_mask, "AST"].apply(
        lambda ast_result: extract_from_ast(ast_result, extract_where_list)
    )

    # df.drop(columns=["SELECT_STATEMENT_CLEANED", "AST"], inplace=True)
    
    df = df[["SELECT_STATEMENT", "TABLE", "COLUMN", "WHERE"]]
    df.to_excel(output_path, index=False)
    # print(df)

if __name__ == "__main__":
    main()
