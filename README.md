# Tài liệu kiến trúc & luồng xử lý

## 1. Kiến trúc tổng quan

Hệ thống có nhiệm vụ trích xuất thông tin **CATALOG / SCHEMA / TABLE / COLUMN** từ câu lệnh SQL (Oracle dialect), đồng thời gắn **CLAUSE** (mệnh đề chứa cột) và duy trì logic suy luận “legacy” khi bảng không rõ ràng. Kiến trúc được chia theo pipeline 3 tầng chính:

- **Parse & chuẩn hoá**: Nhận SQL, mask placeholder, parse thành AST bằng `sqlglot`.
- **Extractor theo stage**: Chạy tuần tự 3 stage (QueryBlock → CatalogTable → CatalogColumn) để ánh xạ cấu trúc truy vấn, nguồn bảng, và cột.
- **Output & logging**: Chuẩn hoá output, sắp xếp, ghi Excel; đồng thời log chi tiết và lưu JSONL trung gian.

Điểm nhấn kiến trúc:

- **Tách stage rõ ràng** để tránh phụ thuộc vòng và dễ debug.
- **Context-driven resolution**: Mọi suy luận cột dựa trên `SelectContext` (alias map, subquery map, CTE index,…).
- **Legacy fallback**: Khi không resolve trực tiếp, sử dụng các fallback (text alias, first-table, single-table-in-subquery, …) để giảm UNRESOLVED.
- **Output bổ sung CATALOG + CLAUSE** nhưng vẫn giữ format cũ cho SCHEMA/TABLE/COLUMN.

## 2. Luồng xử lý

Luồng xử lý chính (đọc Excel → trích xuất → output) như sau:

1) **Đọc input Excel**
   - `io_excel.read_excel_data()` đọc file và chuẩn hoá `SELECT_STATEMENT_CLEANED`.

2) **Parse SQL → AST**
   - `parser.parse_select_statement()` mask placeholder (để sqlglot parse ổn định), rồi parse theo Oracle dialect.
   - Trả về `ParseResult` gồm `ast`, `placeholder_map`, `error`.

3) **Trích xuất theo stage**
   - `extractor.extract_schema_table_column_rows()` chạy tuần tự:
     - **QueryBlockStage**: duyệt AST, tạo các block (ROOT_SELECT/CTE/SUBQUERY/UNION/…) và map node → block.
     - **CatalogTableStage**: cho từng SELECT, tạo danh sách nguồn (PHYSICAL_TABLE/CTE/SUBQUERY), đồng thời tạo `SelectContext`.
     - **CatalogColumnStage**: duyệt mọi mệnh đề (PROJECTION/WHERE/JOIN/HAVING/GROUP/ORDER/WINDOW/QUALIFY), resolve cột bằng resolver.

4) **Resolve cột (legacy + fallback)**
   - `extractor.resolvers._resolve_column_rows()` là trung tâm suy luận, ưu tiên resolve theo alias/table trực tiếp, rồi fallback:
     - CTE/subquery tracing
     - text alias / text table (từ SQL text)
     - first-table fallback
     - single-table-in-subquery
     - derived column → `dual`
   - Kết quả trả về luôn có `CATALOG/SCHEMA/TABLE/COLUMN/REASON`.

5) **Tổng hợp output**
   - `pipeline.build_output_dataframe()` nhận rows, thêm `CLAUSE`, `SELECT_STATEMENT`, sort theo `CATALOG/SCHEMA/TABLE/COLUMN`.
   - `main.py` ghi Excel output.

6) **Logging & stage outputs**
   - Ghi log chi tiết vào `./logs/app.log` (qua `get_logger`).
   - Ghi JSONL trung gian ở `output/temp/`:
     - `query_blocks.jsonl`
     - `catalog_tables.jsonl`
     - `catalog_columns.jsonl`

## 3. Các case xử lý & edge case

Hệ thống xử lý cả các trường hợp phổ biến lẫn các tình huống khó resolve. Dưới đây là các case chính (theo thứ tự ưu tiên/nhánh xử lý quan trọng):

### 3.1. Case resolve cột (ưu tiên trực tiếp → fallback)

**Ưu tiên trực tiếp:**
- **Alias/table trực tiếp**: `alias.column` hoặc `table.column` map qua `alias_map`.
- **Chỉ 1 bảng trong FROM**: nếu không có alias, suy luận về bảng duy nhất.

**Fallback khi không resolve trực tiếp:**
- **CTE/Subquery tracing**: đi vào CTE/subquery để tìm nguồn cột.
- **Text alias fallback**: parse SQL text để map alias khi AST thiếu thông tin.
- **Text table fallback**: dùng danh sách bảng suy ra từ text nếu chỉ có 1 bảng hợp lệ.
- **First-table fallback**: nếu nhiều bảng nhưng chưa rõ, chọn bảng đầu tiên.
- **Single-table-in-subquery**: nếu subquery chỉ có 1 bảng, dùng bảng đó.
- **Subquery-any-table fallback**: khi subquery có nhiều bảng, lấy bảng đầu tiên như fallback.

### 3.2. Case derived & ORDER BY alias

- **Derived column**: nếu cột là biểu thức không chứa column thật, gắn `TABLE=dual` và `REASON=DERIVED_COLUMN`.
- **ORDER BY theo alias**: nếu ORDER BY dùng alias của projection:
  - Nếu alias là biểu thức thuần (không có column), gán `dual`.
  - Nếu alias trỏ tới column thật, resolve theo column đó.

### 3.3. Case UNION / SET operations

- **UNION / INTERSECT / EXCEPT**: QueryBlock tách left/right. Resolver merge kết quả từ các nhánh và dedup theo (CATALOG/SCHEMA/TABLE/COLUMN).

### 3.4. Case STAR

- **`SELECT *` với alias**: dùng `star_aliases` để map về bảng/subquery tương ứng.
- **`SELECT *` không alias**: nếu chỉ 1 bảng thì resolve về bảng đó.
- **`SELECT *` + subquery**: cố resolve single-table-in-subquery trước, nếu không được thì fallback hợp lý.

### 3.5. Case subquery + alias mơ hồ

- **SUBQUERY_ALIAS_FALLBACK**: nếu alias được dùng như table nhưng không resolve được, trả về alias như TABLE.
- **SUBQUERY_SOURCE_ONLY**: khi nguồn chỉ là subquery và không có alias map.

### 3.6. Edge case & an toàn

- **CTE vòng lặp**: có guard visited để tránh recursion vô hạn khi collect tables.
- **Identifier đặc biệt**: placeholder được mask và restore; bracket identifiers có thể không parse được trong Oracle dialect.
- **UNRESOLVED**: nếu không suy luận được bảng, trả `REASON=UNRESOLVED_TABLE` hoặc `SUBQUERY_UNRESOLVED`.

## 4. Mô tả từng module

### Entry & Pipeline
- **`main.py`**: Entry point. Đọc Excel, gọi pipeline, filter/sort, ghi output Excel.
- **`pipeline.py`**: Build DataFrame output. Chạy extractor, thêm CATALOG/CLAUSE, sort theo key.

### Parse & Models
- **`parser.py`**: Parse SQL SELECT. Mask placeholder, parse bằng `sqlglot` (Oracle dialect).
- **`models.py`**: `ParseResult` dataclass chứa `ast/placeholder_map/error`.

### Extractor Core
- **`extractor/extractors.py`**: API trích xuất chính. Chạy 3 stage, ghi JSONL, trả rows chuẩn hoá.
- **`extractor/context.py`**: Build `SelectContext`, alias/subquery/CTE index, utilities (star alias, text alias). Có guard chống recursion khi collect table.
- **`extractor/resolvers.py`**: Logic resolve cột (direct + legacy fallback), xử lý derived/CTE/subquery tracing.
- **`extractor/formatting.py`**: Helper định dạng SQL, restore placeholder, format output name.
- **`extractor/ast_utils.py`**: Helper duyệt AST (select/table/column/children).

### Stage layer
- **`extractor/stages/query_block.py`**: Tạo QueryBlock (structure tree + ordinal + raw_sql).
- **`extractor/stages/catalog_table.py`**: Thu thập nguồn bảng/subquery/CTE theo block.
- **`extractor/stages/catalog_column.py`**: Thu thập cột theo mọi mệnh đề và resolve qua resolver.

### IO & Logging
- **`io_excel.py`**: Đọc/ghi Excel, chuẩn hoá câu SQL.
- **`logger.py`**: Wrapper logging chuẩn hoá format, hỗ trợ ghi file (`./logs`).

---

**Ghi chú**: Output cuối vẫn giữ format legacy (SCHEMA/TABLE/COLUMN) nhưng đã bổ sung **CATALOG** và **CLAUSE** để phản ánh nguồn và mệnh đề chứa cột.
