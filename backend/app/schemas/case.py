from pydantic import BaseModel, Field


class CaseFilter(BaseModel):
    """Filters mirror the SaaS user-behavior detail table."""

    created_start: str | None = None
    created_end: str | None = None
    function_type: str | None = None       # 功能类型
    has_file: bool | None = None           # 是否带文件
    user_rating: str | None = None         # 用户评价（好/差评）
    keyword: str | None = None             # 用户提问关键词

    # 数据处理
    dedup: bool = False                    # 自动去重（同用户问题）
    exclude_failed: bool = True            # 默认只看 FINISH，显著减少扫描量

    # Extra dynamic conditions: [{"field": "...", "value": "..."}]
    extra: list[dict] = Field(default_factory=list)


class CaseItem(BaseModel):
    kind: str                              # qa | review
    task_id: str
    function_module: str | None = None     # 功能模块 (source)
    sub_function: str | None = None        # 二级功能 (dataset 暂无, 预留)
    question: str | None = None            # 用户提问
    attachment: str | None = None          # 附件 (QA=doc_ids / review=首个文件)
    has_file: bool = False                 # 是否带文件
    rating: str | None = None              # 好差评: good / bad / none
    result_score: int | None = None        # 原始分: 1好/0差/2未知/None无
    system_answer: str | None = None       # 系统回答（历史基线, QA 才有）
    is_empty_result: bool | None = None    # 结果是否为空
    stance: dict | None = None             # 审查立场/持方页 (review 才有)
    channel_type: str | None = None        # 渠道: PC / H5 / APP / MINI 等
    created_at: str | None = None


class CasePage(BaseModel):
    total: int
    items: list[CaseItem]


class CaseIdsResponse(BaseModel):
    total: int              # 命中总数
    ids: list[str]          # 命中的 task_id（最多 500）
    capped: bool            # total 是否超过 500 上限
