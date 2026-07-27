"""Live SaaS task sources for case list / hydration.

List path (two-phase):
  1. LIST — light columns only, ORDER BY created DESC LIMIT N, filters pushed down
  2. PAGE HYDRATE — score / answer / review filename for the current page ids only

Callers run sources in parallel, merge in Python, then hydrate the page slice.
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
    "file_review": ["file_review", "library_file_review"],
}

SOURCE_ORDER = [
    "legal_research",
    "document_draft",
    "case_ai",
    "law_ai",
    "contract_review",
    "file_review",
]

# 二级功能 → 收窄 source / document_draft 子条件
_SUBFUNC_TO_SOURCE = {
    "AI类案": "case_ai",
    "AI搜法": "law_ai",
}
_DOC_SUBFUNCS = frozenset({"传统文书", "要素式"})

COUNT_CAP = 5000


@dataclass(frozen=True)
class SourceFilters:
    created_start: date | datetime | None = None
    created_end: date | datetime | None = None
    keyword: str | None = None
    has_file: bool | None = None
    user_rating: str | None = None  # good | bad | None
    exclude_failed: bool = True
    task_id: str | None = None
    channel_type: str | None = None
    sub_function: str | None = None  # 传统文书 / 要素式 / AI类案 / AI搜法


def parse_extra(extra: list[dict] | None) -> dict[str, str]:
    """Flatten CaseFilter.extra into {field: value}; last value wins per field."""
    out: dict[str, str] = {}
    if not extra:
        return out
    for item in extra:
        if not isinstance(item, dict):
            continue
        f = (item.get("field") or "").strip()
        v = item.get("value")
        if not f or v is None or str(v).strip() == "":
            continue
        out[f] = str(v).strip()
    return out


def _doc_ids_has_file(col: str) -> str:
    """QA 带文件 = doc_ids holds a non-empty JSON id list."""
    return f"({col} IS NOT NULL AND btrim({col}) NOT IN ('', '[]', 'null'))"


# 文书起草 = 传统文书 ∪ 要素式。
_DOC_DRAFT_MODULE_COND = (
    "((t.module = 'document_assistant' AND t.doc_generation_mode = 'document_assistant') "
    "OR (t.module = 'document_draft' AND t.doc_generation_mode <> 'document_assistant'))"
)
_DOC_TRADITIONAL = (
    "(t.module = 'document_assistant' AND t.doc_generation_mode = 'document_assistant')"
)
_DOC_ELEMENT = (
    "(t.module = 'document_draft' AND t.doc_generation_mode <> 'document_assistant')"
)
_DOC_DRAFT_SUBFUNC = "CASE WHEN t.module = 'document_draft' THEN '要素式' ELSE '传统文书' END"

# 要素式常常没有用户提问；此时给 Agent 一个固定任务指令，否则它不知道要做什么。
_DOC_ELEMENT_FALLBACK_Q = "帮我基于这个文件生成要素式文书"
_DOC_DRAFT_QUESTION = (
    f"CASE WHEN {_DOC_ELEMENT} AND NULLIF(btrim(p.prompt_content), '') IS NULL "
    f"THEN '{_DOC_ELEMENT_FALLBACK_Q}' ELSE p.prompt_content END"
)

_AI_SUBFUNC = {"case_ai": "AI类案", "law_ai": "AI搜法"}


def _score_sql(schema: str, source: str, task_key: str) -> str:
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


def _contract_answer_sql(schema: str, task_alias: str = "t", *, max_len: int | None = None) -> str:
    """合同审查系统回答：对齐 v2/task/result 卡片 title + reason(risk)。"""
    agg = f"""(
        SELECT string_agg(
                 NULLIF(btrim(concat_ws(
                   E'\\n',
                   NULLIF(btrim(r.title), ''),
                   NULLIF(btrim(r.reason), '')
                 )), ''),
                 E'\\n\\n'
                 ORDER BY r.id
               )
          FROM {schema}.t_contract_result_info r
         WHERE r.task_id = {task_alias}.task_id AND r.is_delete = 0
    )"""
    if max_len:
        return f"LEFT({agg}, {int(max_len)})"
    return agg


def _file_review_answer_sql(task_alias: str = "t", *, max_len: int | None = None) -> str:
    """文件审查系统回答：优先 final_result，否则 review_report。"""
    expr = f"""NULLIF(btrim(COALESCE(
        NULLIF(btrim({task_alias}.final_result), ''),
        {task_alias}.review_report
    )), '')"""
    if max_len:
        return f"LEFT({expr}, {int(max_len)})"
    return expr


def _pushdown_clauses(
    source: str,
    *,
    created_col: str,
    question_col: str,
    task_key: str,
    task_id_col: str,
    channel_col: str,
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
    if filters.task_id:
        conds.append(f"{task_id_col} = :task_id")
        params["task_id"] = filters.task_id
    if filters.channel_type:
        conds.append(f"{channel_col} = :channel_type")
        params["channel_type"] = filters.channel_type
    return conds, params


def _and(conds: list[str]) -> str:
    return (" AND " + " AND ".join(conds)) if conds else ""


def _limit_sql(per_source_limit: int | None) -> str:
    if not per_source_limit or per_source_limit <= 0:
        return ""
    return f"LIMIT {int(per_source_limit)}"


def resolve_sources(
    function_type: str | None,
    *,
    sub_function: str | None = None,
) -> list[str]:
    """Resolve which sources to query; sub_function may further narrow the set."""
    if function_type:
        if function_type not in SOURCE_ORDER:
            return []
        sources = [function_type]
    else:
        sources = list(SOURCE_ORDER)

    if not sub_function:
        return sources

    if sub_function in _SUBFUNC_TO_SOURCE:
        want = _SUBFUNC_TO_SOURCE[sub_function]
        return [want] if want in sources else []

    if sub_function in _DOC_SUBFUNCS:
        return ["document_draft"] if "document_draft" in sources else []

    # Unknown sub_function → no rows (avoid silent broad scan)
    return []


def _doc_module_cond(filters: SourceFilters) -> str:
    if filters.sub_function == "传统文书":
        return _DOC_TRADITIONAL
    if filters.sub_function == "要素式":
        return _DOC_ELEMENT
    return _DOC_DRAFT_MODULE_COND


def build_list_source_sql(
    schema: str,
    source: str,
    *,
    filters: SourceFilters,
    per_source_limit: int,
) -> tuple[str, dict]:
    """Light list row — no system_answer / score aggregation."""
    lim = _limit_sql(per_source_limit)

    if source == "legal_research":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.question",
            task_key="t.chat_id", task_id_col="t.chat_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT 'qa' AS kind, 'legal_research' AS source, t.chat_id AS task_id,
               t.question AS question, t.created AS src_created,
               NULL::text AS sub_function,
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
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT 'qa' AS kind, 'document_draft' AS source, t.task_id AS task_id,
               p.prompt_content AS question, t.created AS src_created,
               {_DOC_DRAFT_SUBFUNC} AS sub_function,
               t.doc_ids AS doc_ids, t.project_id AS project_id,
               {has_file} AS has_file, t.channel_type AS channel_type
        FROM {schema}.t_document_task t
        LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
        WHERE t.is_delete = 0
          AND {_doc_module_cond(filters)}
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
            task_key="h.task_id", task_id_col="h.task_id", channel_col="h.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND h.status = 'FINISH'" if filters.exclude_failed else ""
        sub_func = _AI_SUBFUNC[source]
        sql = f"""
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
        """
        return sql, params

    if source == "contract_review":
        has_file = _review_has_file_sql(schema)
        # Only compute EXISTS when filtering; otherwise assume review tasks have files.
        list_has_file = has_file if filters.has_file is not None else "TRUE"
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.task_name",
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT 'review' AS kind, 'contract_review' AS source, t.task_id AS task_id,
               t.task_name AS question, t.created AS src_created,
               NULL::text AS sub_function,
               NULL::text AS doc_ids, NULL::text AS project_id,
               {list_has_file} AS has_file, t.channel_type AS channel_type
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
        list_has_file = has_file if filters.has_file is not None else "TRUE"
        qcol = "COALESCE(t.origin_name, t.task_name)"
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col=qcol,
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT 'review' AS kind, 'file_review' AS source, t.task_id AS task_id,
               {qcol} AS question, t.created AS src_created,
               NULL::text AS sub_function,
               NULL::text AS doc_ids, NULL::text AS project_id,
               {list_has_file} AS has_file, t.channel_type AS channel_type
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
    cap: int = COUNT_CAP,
) -> tuple[str, dict]:
    """Count at most `cap` rows per source so COUNT cannot scan the whole table."""
    lim = int(cap)

    if source == "legal_research":
        has_file = _doc_ids_has_file("t.doc_ids")
        extra, params = _pushdown_clauses(
            source, created_col="t.created", question_col="t.question",
            task_key="t.chat_id", task_id_col="t.chat_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
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
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
        )
        status = "AND t.task_status = 'FINISH'" if filters.exclude_failed else ""
        sql = f"""
        SELECT count(*)::bigint AS c FROM (
            SELECT 1
            FROM {schema}.t_document_task t
            LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
            WHERE t.is_delete = 0
              AND {_doc_module_cond(filters)}
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
            task_key="h.task_id", task_id_col="h.task_id", channel_col="h.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
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
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
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
            task_key="t.task_id", task_id_col="t.task_id", channel_col="t.channel_type",
            has_file_expr=has_file, filters=filters, schema=schema,
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


def build_page_hydrate_sql(
    schema: str,
    source: str,
) -> str:
    """Hydrate score / answer / review attachment for a page of task_ids."""
    if source == "legal_research":
        return f"""
        SELECT t.chat_id AS task_id,
               {_score_sql(schema, 'legal_research', 't.chat_id')} AS result_score,
               t.llm_answer AS system_answer,
               NULL::text AS attachment,
               {_doc_ids_has_file('t.doc_ids')} AS has_file
        FROM {schema}.t_legal_research_info t
        WHERE t.chat_id = ANY(:ids)
        """

    if source == "document_draft":
        answer = """NULLIF(btrim(regexp_replace(t.result, '^.*?zhiexa_reasoning_end', '', 's')), '')"""
        return f"""
        SELECT t.task_id AS task_id,
               {_score_sql(schema, 'document_draft', 't.task_id')} AS result_score,
               {answer} AS system_answer,
               NULL::text AS attachment,
               {_doc_ids_has_file('t.doc_ids')} AS has_file
        FROM {schema}.t_document_task t
        WHERE t.task_id = ANY(:ids)
        """

    if source in ("case_ai", "law_ai"):
        preview = f"""(SELECT string_agg(o.content, '' ORDER BY o.id)
                        FROM {schema}.t_fuxi_task_output o
                       WHERE o.task_id = h.task_id AND o.type = 'analysis')"""
        return f"""
        SELECT h.task_id AS task_id,
               {_score_sql(schema, source, 'h.task_id')} AS result_score,
               {preview} AS system_answer,
               NULL::text AS attachment,
               {_doc_ids_has_file('h.doc_ids')} AS has_file
        FROM {schema}.t_fuxi_history_task h
        WHERE h.task_id = ANY(:ids) AND h.type = '{source}'
        """

    if source == "contract_review":
        att = f"""(SELECT f.file_name FROM {schema}.t_file_info f
           WHERE f.task_id = t.task_id AND f.is_delete = 0
             AND f.file_type = 'original_file' AND f.file_version = 1
           ORDER BY f.created DESC LIMIT 1)"""
        return f"""
        SELECT t.task_id AS task_id,
               {_score_sql(schema, 'contract_review', 't.task_id')} AS result_score,
               {_contract_answer_sql(schema, max_len=8000)} AS system_answer,
               COALESCE({att}, t.task_name) AS attachment,
               {_review_has_file_sql(schema)} AS has_file
        FROM {schema}.t_contract_tasks t
        WHERE t.task_id = ANY(:ids)
        """

    if source == "file_review":
        att = f"""(SELECT f.file_name FROM {schema}.t_file_info f
           WHERE f.task_id = t.task_id AND f.is_delete = 0
             AND f.file_type = 'original_file' AND f.file_version = 1
           ORDER BY f.created DESC LIMIT 1)"""
        qcol = "COALESCE(t.origin_name, t.task_name)"
        return f"""
        SELECT t.task_id AS task_id,
               {_score_sql(schema, 'file_review', 't.task_id')} AS result_score,
               {_file_review_answer_sql(max_len=8000)} AS system_answer,
               COALESCE({att}, {qcol}) AS attachment,
               {_review_has_file_sql(schema)} AS has_file
        FROM {schema}.t_file_review_task t
        WHERE t.task_id = ANY(:ids)
        """

    raise ValueError(f"unknown source: {source}")


# ---------------------------------------------------------------------------
# HYDRATE (full fields, filtered by task_ids) — task creation / download
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
               {_DOC_DRAFT_QUESTION} AS question,
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
               t.task_name AS question,
               {_contract_answer_sql(schema)} AS system_answer,
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
               COALESCE(t.origin_name, t.task_name) AS question,
               {_file_review_answer_sql()} AS system_answer,
               NULL AS attachment, NULL::text AS project_id, {orig} AS original_file,
               {refs} AS reference_files, ({detail})->>'url' AS detail_annotated_file,
               jsonb_build_object('custom_require', t.custom_require) AS stance
        FROM {schema}.t_file_review_task t
        WHERE t.is_delete = 0 AND t.task_id = ANY(:ids)
        """

    raise ValueError(f"unknown source: {source}")
