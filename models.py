from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from sqlglot import expressions as exp


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Kết quả parse một câu lệnh SQL SELECT.

    Attributes:
        ast: AST đã parse hoặc None nếu parse thất bại.
        placeholder_map: Ánh xạ placeholder đã mask về token gốc.
        error: Thông báo lỗi khi parse thất bại, ngược lại là None.
    """

    ast: Optional[exp.Expression]
    placeholder_map: Dict[str, str]
    error: Optional[str]
