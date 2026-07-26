"""Build the 评测结果 Excel for a task (openpyxl). No 幻觉数 column."""
import io

from openpyxl import Workbook

from app.models.eval_task import EvalTask, EvalTaskCase

HEADERS = [
    "任务名称",
    "评测Workflow",          # coze 工作流 id
    "用例ID",
    "功能模块",
    "用户提问",
    "旧版答案(基线)",
    "新版答案(Agent)",
    "旧版评分",
    "新版评分",
    "旧版文件解析评分",
    "新版文件解析评分",
    "是否胜出",
    "评分理由",
    "Coze执行链接",
    "Agent耗时",
    "状态",
    "错误",
]


def _format_latency(ms: int | float | None) -> str:
    """毫秒 → 可读分秒，如 65000 → 1分5秒；不足 1 分则 45秒。"""
    if ms is None:
        return ""
    try:
        total_sec = max(0, int(round(float(ms) / 1000.0)))
    except (TypeError, ValueError):
        return ""
    minutes, seconds = divmod(total_sec, 60)
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def _row(task: EvalTask, c: EvalTaskCase) -> list:
    cr = c.compare_result or {}
    return [
        task.name,
        task.eval_workflow_id,
        c.source_case_id,
        c.source or "",
        c.question or "",
        c.baseline_answer or "",
        c.agent_output or "",
        cr.get("score_old"),
        cr.get("score_new"),
        cr.get("file_score_old"),
        cr.get("file_score_new"),
        "是" if c.is_win else ("否" if c.is_win is not None else ""),
        cr.get("score_reason") or "",
        c.coze_exec_url or "",
        _format_latency(c.agent_latency_ms),
        c.stage.value if c.stage is not None else "",
        c.error_msg or "",
    ]


def build_result_xlsx(task: EvalTask, cases: list[EvalTaskCase]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "评测结果"
    ws.append(HEADERS)
    for c in cases:
        ws.append(_row(task, c))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
