"""Live SaaS task sources for case list / hydration.

List path is intentionally minimal and per-source:
  - QA sources return full system_answer as stored
  - result_score from t_feedback_record (0差/1好/2未知/NULL无)
  - each source uses ORDER BY created DESC LIMIT N
  - callers run sources in parallel and merge in Python
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

FEEDBACK_APPS = {
    "legal_research": ["legal_research", "library_legal_research"],
    "document_draft": ["document_assistant", "library_document_assistant"],
    "case_ai": ["ai_case", "library_fuxi_search_case_ai"],
    "law_ai": ["ai_law", "library_fuxi_search_law_ai"],
    "contract_review": ["contract_review", "library_contract_review"],
    "file_review": ["file_review"],
}

SOURCE_ORDER = [
    "legal_research",
    "document_draft",
    "case_ai",
    "law_ai",
    "contract_review",
    "file_review",
]


@dataclass(frozen=True)
class SourceFilters:
    created_start: date | datetime | None = None
    created_end: date | datetime | None = None
    keyword: str | None = None
    has_file: bool | None = None
    user_rating: str | None = None  # good | bad | None
    exclude_failed: bool = True


def _doc_ids_has_file(col: str) -> str:
    """QA 带文件 = doc_ids holds a non-empty JSON id list.

    doc_ids is stored as a JSON string; '', '[]' and 'null' all mean "no file"
    (matches resolve_oss_urls, which skips '[]'). Plain `<> ''` wrongly reported
    带文件 for tasks whose doc_ids is the empty array '[]'.
    """
    return f"({col} IS NOT NULL AND btrim({col}) NOT IN ('', '[]', 'null'))"


# 文书起草 = 传统文书 ∪ 要素式。
#   传统文书: module='document_assistant' 且 doc_generation_mode='document_assistant'
#   要素式:   module='document_draft'     且 doc_generation_mode<>'document_assistant'
_DOC_DRAFT_MODULE_COND = (
    "((t.module = 'document_assistant' AND t.doc_generation_mode = 'document_assistant') "
    "OR (t.module = 'document_draft' AND t.doc_generation_mode <> 'document_assistant'))"
)
# 二级功能：在上面的范围内，module='document_draft' 即要素式，其余为传统文书。
_DOC_DRAFT_SUBFUNC = "CASE WHEN t.module = 'document_draft' THEN '要素式' ELSE '传统文书' END"

# AI类案 / AI搜法 归属"法律检索"功能模块，二级功能即其本身。
_AI_SUBFUNC = {"case_ai": "AI类案", "law_ai": "AI搜法"}


def _score_sql(schema: str, source: str, task_key: str) -> str:
    """Scalar subquery: result_score for task (0差/1好/2未知/NULL无反馈)."""
    apps = ", ".join(f"'{a}'" for a in FEEDBACK_APPS[source])
    return f"""(SELECT fr.result_score
       FROM {schema}.t_feedback_record fr
      WHERE fr.task_id = {task_key} AND fr.is_delete = 0
        AND fr.application IN ({apps})
      LIMIT 1)"""


def _rating_exists(schema: str, source: str, task_key: str, rating: str) -> str:
    score = 1 if rating == "good" else 0
    apps = ", ".join(f"'{a}'" for a in FEEDBACK_APPS[source])
    return f"""EXISTS (
        SELECT 1 FROM {schema}.t_feedback_record fr
         WHERE fr.task_id = {task_key} AND fr.is_delete = 0
           AND fr.application IN ({apps}) AND fr.result_score = {score}
    )"""


def _review_has_file_sql(schema: str) -> str:
    return f"""EXISTS (
        SELECT 1 FROM {schema}.t_file_info f
         WHERE f.task_id = t.task_id AND f.is_delete = 0
           AND f.file_type = 'original_file' AND f.file_version = 1
    )"""


def _pushdown_clauses(
    source: str,
    *,
    created_col: str,
    question_col: str,
    task_key: str,
    has_file_expr: str,
    filters: SourceFilters,
    schema: str,
) -> tuple[list[str], dict]:
    conds: list[str] = []
    params: dict = {}
    if filters.created_start:
        conds.append(f"{created_col} >= :created_start")
        params["created_start"] = filters.created_start
    if filters.created_end:
        conds.append(f"{created_col} <= :created_end")
        params["created_end"] = filters.created_end
    if filters.keyword:
        conds.append(f"{question_col} ILIKE :keyword")
        params["keyword"] = f"%{filters.keyword}%"
    if filters.has_file is True:
        conds.append(f"({has_file_expr})")
    elif filters.has_file is False:
        conds.append(f"NOT ({has_file_expr})")
    if filters.user_rating in ("good", "bad"):
        conds.append(_rating_exists(schema, source, task_key, filters.user_rating))
    return conds, params


def _and(conds: list[str]) -> str:
    return (" AND " + " AND ".join(conds)) if conds else ""


def _limit_sql(per_source_limit: int | None) -> str:
    if not per_source_limit or per_source_limit <= 0:
        return ""
    return f"LIMIT {int(per_source_limit)}"


def resolve_sources(function_type: str | None) -> list[str]:
    if not function_type:
        return list(SOURCE_ORDER)
    if function_type not in SOURCE_ORDER:
        return []
    return [function_type]


def build_list_source_sql(
    schema: str,
    source: str,
    *,
    filters: SourceFilters,
    per_source_limit: int,
) -> tuple[str, dict]:
    """List row including full system_answer (QA sources)."""
    lim = _limit_sql(per_source_limit)

    if source == "legal_research":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.question",
            task_key="t.chat_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT 'qa' AS kind, 'legal_research' AS source, t.chat_id AS task_id,
               t.question AS question, t.created AS src_created,
               {_score_sql(schema, 'legal_research', 't.chat_id')} AS result_score,
               t.llm_answer AS system_answer,
               t.doc_ids AS doc_ids, t.project_id AS project_id,
               {has_file} AS has_file, t.channel_type AS channel_type
        FROM {schema}.t_legal_research_info t
        WHERE t.is_delete = 0
          AND (t.parent_chat_id IS NULL OR t.parent_chat_id = t.chat_id)
          {status}
          {_and(extra)}
        ORDER BY t.created DESC
        {lim}
        """
        return sql, params

    if source == "document_draft":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="p.prompt_content",
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        # Strip reasoning prefix when present (same as export script).
        answer = """NULLIF(btrim(regexp_replace(t.result, '^.*?zhiexa_reasoning_end', '', 's')), '')"""
        sql = f"""
        SELECT 'qa' AS kind, 'document_draft' AS source, t.task_id AS task_id,
               p.prompt_content AS question, t.created AS src_created,
               {_score_sql(schema, 'document_draft', 't.task_id')} AS result_score,
               {answer} AS system_answer,
               {_DOC_DRAFT_SUBFUNC} AS sub_function,
               t.doc_ids AS doc_ids, t.project_id AS project_id,
               {has_file} AS has_file, t.channel_type AS channel_type
        FROM {schema}.t_document_task t
        LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
        WHERE t.is_delete = 0
          AND {_DOC_DRAFT_MODULE_COND}
          AND t.parent_task_id = t.task_id
          {status}
          {_and(extra)}
        ORDER BY t.created DESC
        {lim}
        """
        return sql, params

    if source in ("case_ai", "law_ai"):
        has_file = _doc_ids_has_file("h.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="h.created_at", question_col="h.original_question",
            task_key="h.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND h.status = 'FINISH'" if filters.exclude_failed else ""
        # Fetch answer / score only for the limited rows (outer query).
        preview = f"""(SELECT string_agg(o.content, '' ORDER BY o.id)
                        FROM {schema}.t_fuxi_task_output o
                       WHERE o.task_id = b.task_id AND o.type = 'analysis')"""
        score = _score_sql(schema, source, "b.task_id")
        sub_func = _AI_SUBFUNC[source]
        sql = f"""
        SELECT b.kind, b.source, b.task_id, b.question, b.src_created,
               {score} AS result_score, {preview} AS system_answer,
               b.sub_function, b.doc_ids, b.project_id, b.has_file, b.channel_type
        FROM (
            SELECT 'qa' AS kind, '{source}' AS source, h.task_id AS task_id,
                   h.original_question AS question, h.created_at AS src_created,
                   '{sub_func}' AS sub_function,
                   h.doc_ids AS doc_ids, h.project_id AS project_id,
                   {has_file} AS has_file, h.channel_type AS channel_type
            FROM {schema}.t_fuxi_history_task h
            WHERE h.is_deleted = 0 AND h.type = '{source}'
              AND h.parent_task_id IS NULL
              {status}
              {_and(extra)}
            ORDER BY h.created_at DESC
            {lim}
        ) b
        """
        return sql, params

    if source == "contract_review":
        has_file = _review_has_file_sql(schema)
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.task_name",
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        # Always report the real 带文件 state (EXISTS on t_file_info), even when the
        # has_file filter is off — otherwise every review task falsely shows 带文件.
        sql = f"""
        SELECT 'review' AS kind, 'contract_review' AS source, t.task_id AS task_id,
               t.task_name AS question, t.created AS src_created,
               {_score_sql(schema, 'contract_review', 't.task_id')} AS result_score,
               NULL::text AS system_answer,
               t.task_name AS attachment,
               {has_file} AS has_file, t.channel_type AS channel_type
        FROM {schema}.t_contract_tasks t
        WHERE t.is_delete = 0
          {status}
          {_and(extra)}
        ORDER BY t.created DESC
        {lim}
        """
        return sql, params

    if source == "file_review":
        has_file = _review_has_file_sql(schema)
        qcol = "COALESCE(t.origin_name, t.task_name)"
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col=qcol,
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        # Always report the real 带文件 state (EXISTS on t_file_info), even when the
        # has_file filter is off — otherwise every review task falsely shows 带文件.
        sql = f"""
        SELECT 'review' AS kind, 'file_review' AS source, t.task_id AS task_id,
               {qcol} AS question, t.created AS src_created,
               {_score_sql(schema, 'file_review', 't.task_id')} AS result_score,
               NULL::text AS system_answer,
               {qcol} AS attachment,
               {has_file} AS has_file, t.channel_type AS channel_type
        FROM {schema}.t_file_review_task t
        WHERE t.is_delete = 0
          {status}
          {_and(extra)}
        ORDER BY t.created DESC
        {lim}
        """
        return sql, params

    raise ValueError(f"unknown source: {source}")


def build_capped_count_sql(
    schema: str,
    source: str,
    *,
    filters: SourceFilters,
    cap: int = 5000,
) -> tuple[str, dict]:
    """Count at most `cap` rows per source so COUNT cannot scan the whole table."""
    lim = int(cap)

    if source == "legal_research":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.question",
            task_key="t.chat_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1 FROM {schema}.t_legal_research_info t
            WHERE t.is_delete = 0
              AND (t.parent_chat_id IS NULL OR t.parent_chat_id = t.chat_id)
              {status}
              {_and(extra)}
            LIMIT {lim}
        ) x
        """
        return sql, params

    if source == "document_draft":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="p.prompt_content",
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1
            FROM {schema}.t_document_task t
            LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
            WHERE t.is_delete = 0
              AND {_DOC_DRAFT_MODULE_COND}
              AND t.parent_task_id = t.task_id
              {status}
              {_and(extra)}
            LIMIT {lim}
        ) x
        """
        return sql, params

    if source in ("case_ai", "law_ai"):
        has_file = _doc_ids_has_file("h.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="h.created_at", question_col="h.original_question",
            task_key="h.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND h.status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1 FROM {schema}.t_fuxi_history_task h
            WHERE h.is_deleted = 0 AND h.type = '{source}'
              AND h.parent_task_id IS NULL
              {status}
              {_and(extra)}
            LIMIT {lim}
        ) x
        """
        return sql, params

    if source == "contract_review":
        has_file = _review_has_file_sql(schema)
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.task_name",
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1 FROM {schema}.t_contract_tasks t
            WHERE t.is_delete = 0
              {status}
              {_and(extra)}
            LIMIT {lim}
        ) x
        """
        return sql, params

    if source == "file_review":
        has_file = _review_has_file_sql(schema)
        qcol = "COALESCE(t.origin_name, t.task_name)"
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col=qcol,
            task_key="t.task_id", has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1 FROM {schema}.t_file_review_task t
            WHERE t.is_delete = 0
              {status}
              {_and(extra)}
            LIMIT {lim}
        ) x
        """
        return sql, params

    raise ValueError(f"unknown source: {source}")


# ---------------------------------------------------------------------------
# HYDRATE (full fields, filtered by task_ids)
# ---------------------------------------------------------------------------

def _review_file_json_sql(schema: str, ftype: str) -> str:
    return f"""(SELECT jsonb_build_object('url', f.file_url, 'name', f.file_name)
       FROM {schema}.t_file_info f
      WHERE f.task_id = t.task_id AND f.is_delete = 0
        AND f.file_type = '{ftype}' AND f.file_version = 1
      ORDER BY f.created DESC LIMIT 1)"""


def _review_reffiles_sql(schema: str) -> str:
    return f"""(SELECT jsonb_agg(jsonb_build_object('url', f.file_url, 'name', f.file_name) ORDER BY f.created)
       FROM {schema}.t_file_info f
      WHERE f.task_id = t.task_id AND f.is_delete = 0
        AND f.file_type = 'reference_file')"""


def _contract_stance_sql(schema: str) -> str:
    return f"""(SELECT jsonb_build_object(
              'review_stance', m.review_stance,
              'subject', (SELECT ri.subject
                            FROM {schema}.t_contract_review_info ri
                           WHERE ri.task_id = t.task_id
                             AND ri.review_stance = m.review_stance
                           LIMIT 1),
              'review_people', m.review_people,
              'review_status', m.review_status,
              'custom_require', m.custom_require)
       FROM {schema}.t_contract_meta m
      WHERE m.contract_id = t.contract_id
      LIMIT 1)"""


def build_hydrate_sql(*, schema: str = "public", function_type: str | None = None) -> str:
    sources = resolve_sources(function_type) or list(SOURCE_ORDER)
    parts = [_hydrate_source_sql(schema, s) for s in sources]
    return "\nUNION ALL\n".join(f"({p})" for p in parts)


def _hydrate_source_sql(schema: str, source: str) -> str:
    if source == "legal_research":
        return f"""
        SELECT 'qa' AS kind, 'legal_research' AS source, t.chat_id AS task_id,
               t.question AS question, t.llm_answer AS system_answer,
               t.doc_ids AS attachment, t.project_id AS project_id, NULL::jsonb AS original_file,
               NULL::jsonb AS reference_files, NULL::text AS detail_annotated_file,
               NULL::jsonb AS stance
        FROM {schema}.t_legal_research_info t
        WHERE t.is_delete = 0
          AND (t.parent_chat_id IS NULL OR t.parent_chat_id = t.chat_id)
          AND t.chat_id = ANY(:ids)
        """

    if source == "document_draft":
        return f"""
        SELECT 'qa' AS kind, 'document_draft' AS source, t.task_id AS task_id,
               p.prompt_content AS question,
               NULLIF(btrim(regexp_replace(t.result, '^.*?zhiexa_reasoning_end', '', 's')), '') AS system_answer,
               t.doc_ids AS attachment, t.project_id AS project_id, NULL::jsonb AS original_file,
               NULL::jsonb AS reference_files, NULL::text AS detail_annotated_file,
               NULL::jsonb AS stance
        FROM {schema}.t_document_task t
        LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
        WHERE t.is_delete = 0
          AND {_DOC_DRAFT_MODULE_COND}
          AND t.parent_task_id = t.task_id
          AND t.task_id = ANY(:ids)
        """

    if source in ("case_ai", "law_ai"):
        answer = f"""(SELECT string_agg(o.content, '' ORDER BY o.id)
                       FROM {schema}.t_fuxi_task_output o
                      WHERE o.task_id = h.task_id AND o.type = 'analysis')"""
        return f"""
        SELECT 'qa' AS kind, '{source}' AS source, h.task_id AS task_id,
               h.original_question AS question, {answer} AS system_answer,
               h.doc_ids AS attachment, h.project_id AS project_id, NULL::jsonb AS original_file,
               NULL::jsonb AS reference_files, NULL::text AS detail_annotated_file,
               NULL::jsonb AS stance
        FROM {schema}.t_fuxi_history_task h
        WHERE h.is_deleted = 0 AND h.type = '{source}'
          AND h.parent_task_id IS NULL
          AND h.task_id = ANY(:ids)
        """

    if source == "contract_review":
        orig = _review_file_json_sql(schema, "original_file")
        detail = _review_file_json_sql(schema, "detail_annotated_file")
        refs = _review_reffiles_sql(schema)
        stance = _contract_stance_sql(schema)
        return f"""
        SELECT 'review' AS kind, 'contract_review' AS source, t.task_id AS task_id,
               t.task_name AS question, NULL AS system_answer,
               NULL AS attachment, NULL::text AS project_id, {orig} AS original_file,
               {refs} AS reference_files, ({detail})->>'url' AS detail_annotated_file,
               {stance} AS stance
        FROM {schema}.t_contract_tasks t
        WHERE t.is_delete = 0 AND t.task_id = ANY(:ids)
        """

    if source == "file_review":
        orig = _review_file_json_sql(schema, "original_file")
        detail = _review_file_json_sql(schema, "detail_annotated_file")
        refs = _review_reffiles_sql(schema)
        return f"""
        SELECT 'review' AS kind, 'file_review' AS source, t.task_id AS task_id,
               COALESCE(t.origin_name, t.task_name) AS question, NULL AS system_answer,
               NULL AS attachment, NULL::text AS project_id, {orig} AS original_file,
               {refs} AS reference_files, ({detail})->>'url' AS detail_annotated_file,
               jsonb_build_object('custom_require', t.custom_require) AS stance
        FROM {schema}.t_file_review_task t
        WHERE t.is_delete = 0 AND t.task_id = ANY(:ids)
        """

    raise ValueError(f"unknown source: {source}")
