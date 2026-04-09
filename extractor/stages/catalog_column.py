from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from sqlglot import expressions as exp

from ast_utils import iter_columns, iter_selects

from ..formatting import _column_output_name, _expression_sql, _output_name
from ..resolvers import _resolve_column_rows


class CatalogColumnStage:
    """Tạo danh sách CatalogColumn theo tất cả mệnh đề."""

    @staticmethod
    def _resolve_clause_expressions(
        select: exp.Select,
    ) -> List[Tuple[str, List[exp.Expression]]]:
        clauses: List[Tuple[str, List[exp.Expression]]] = []
        if select.expressions:
            clauses.append(("PROJECTION", list(select.expressions)))

        where = select.args.get("where")
        if isinstance(where, exp.Where) and where.this is not None:
            clauses.append(("WHERE", [where.this]))

        having = select.args.get("having")
        if isinstance(having, exp.Having) and having.this is not None:
            clauses.append(("HAVING", [having.this]))

        joins = select.args.get("joins") or []
        join_exprs: List[exp.Expression] = []
        for join in joins:
            if not isinstance(join, exp.Join):
                continue
            join_on = join.args.get("on")
            if isinstance(join_on, exp.Expression):
                join_exprs.append(join_on)
        if join_exprs:
            clauses.append(("JOIN_ON", join_exprs))

        group = select.args.get("group")
        if isinstance(group, exp.Group) and group.expressions:
            clauses.append(("GROUP_BY", list(group.expressions)))

        order = select.args.get("order")
        if isinstance(order, exp.Order) and order.expressions:
            clauses.append(("ORDER_BY", list(order.expressions)))

        windows = select.args.get("windows") or []
        if isinstance(windows, exp.Window):
            windows = [windows]
        if isinstance(windows, list) and windows:
            clauses.append(("WINDOW", [item for item in windows if isinstance(item, exp.Expression)]))

        qualify = select.args.get("qualify")
        if isinstance(qualify, exp.Qualify) and qualify.this is not None:
            clauses.append(("QUALIFY", [qualify.this]))

        return clauses

    def run(
        self,
        ast: exp.Expression,
        node_to_block: Dict[int, int],
        select_context_by_block: Dict[int, Any],
        placeholder_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        catalog_columns: List[Dict[str, Any]] = []

        policy = {
            "allow_text_alias": True,
            "allow_text_table": True,
            "allow_first_table": True,
            "allow_table_plus_subquery": True,
        }

        for select in iter_selects(ast):
            block_id = node_to_block.get(id(select))
            if block_id is None:
                continue
            ctx = select_context_by_block.get(block_id)
            if ctx is None:
                continue

            projection_map: Dict[str, List[exp.Expression]] = {}
            for expression in select.expressions or []:
                output_name = _output_name(expression, placeholder_map).strip().lower()
                if not output_name:
                    continue
                projection_map.setdefault(output_name, []).append(expression)

            for clause_type, exprs in self._resolve_clause_expressions(select):
                for expr in exprs:
                    if not isinstance(expr, exp.Expression):
                        continue
                    for column in iter_columns(expr):
                        if not isinstance(column, exp.Column):
                            continue
                        if column.parent_select is not select:
                            continue
                        if bool(getattr(column, "is_star", False)):
                            continue
                        column_name = _column_output_name(column, placeholder_map)

                        alias_key = (column.name or "").strip().lower()
                        if clause_type == "ORDER_BY" and not (column.table or "").strip() and alias_key in projection_map:
                            expressions = projection_map.get(alias_key) or []
                            if len(expressions) == 1:
                                target_expr = expressions[0]
                                if not any(isinstance(col, exp.Column) for col in iter_columns(target_expr)):
                                    catalog_columns.append(
                                        {
                                            "block_id": block_id,
                                            "schema_name": "",
                                            "table_name": "DUAL",
                                            "column_name": column_name,
                                            "reason": "DERIVED_COLUMN",
                                            "clause_type": clause_type,
                                            "raw_sql": _expression_sql(column, placeholder_map, dialect="oracle"),
                                        }
                                    )
                                    continue
                                resolved_rows: List[Dict[str, str]] = []
                                for inner_column in iter_columns(target_expr):
                                    if not isinstance(inner_column, exp.Column):
                                        continue
                                    if bool(getattr(inner_column, "is_star", False)):
                                        continue
                                    inner_name = _column_output_name(inner_column, placeholder_map)
                                    resolved_rows.extend(
                                        _resolve_column_rows(
                                            ctx,
                                            inner_column,
                                            placeholder_map,
                                            inner_name,
                                            policy=policy,
                                        )
                                    )
                                for row in resolved_rows:
                                    catalog_columns.append(
                                        {
                                            "block_id": block_id,
                                            "schema_name": row.get("SCHEMA", ""),
                                            "table_name": row.get("TABLE", ""),
                                            "column_name": column_name,
                                            "reason": row.get("REASON", ""),
                                            "clause_type": clause_type,
                                            "raw_sql": _expression_sql(column, placeholder_map, dialect="oracle"),
                                        }
                                    )
                                continue

                        rows = _resolve_column_rows(
                            ctx,
                            column,
                            placeholder_map,
                            column_name,
                            policy=policy,
                        )
                        for row in rows:
                            catalog_columns.append(
                                {
                                    "block_id": block_id,
                                    "schema_name": row.get("SCHEMA", ""),
                                    "table_name": row.get("TABLE", ""),
                                    "column_name": row.get("COLUMN", ""),
                                    "reason": row.get("REASON", ""),
                                    "clause_type": clause_type,
                                    "raw_sql": _expression_sql(column, placeholder_map, dialect="oracle"),
                                }
                            )

        return catalog_columns
