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
    dedup: bool = True                     # 自动去重（同用户问题）
    exclude_failed: bool = True            # 默认只看 FINISH，显著减少扫描量

    # Extra dynamic conditions: [{"field": "...", "value": "..."}]
    # Supported fields: sub_function | channel_type | task_id
    extra: list[dict] = Field(default_factory=list)


class CaseItem(BaseModel):
    kind: str                              # qa | review
    task_id: str
    function_module: str | None = None     # 功能模块 (source)
    sub_function: str | None = None        # 二级功能
    question: str | None = None            # 用户提问
    attachment: str | None = None          # 附件文件名 (QA=library解析的文件名 / review=任务名或文件名)
    has_file: bool = False                 # 是否带文件
    rating: str | None = None              # 好差评: good / bad / none
    result_score: int | None = None        # 原始分: 1好/0差/2未知/None无
    system_answer: str | None = None       # 系统回答（QA=历史答案；合同=结果卡片摘要；文件审查=报告/final_result）
    is_empty_result: bool | None = None    # 结果是否为空
    stance: dict | None = None             # 审查立场/持方页 (review 才有)
    channel_type: str | None = None        # 渠道: PC / H5 / APP / MINI 等
    created_at: str | None = None


class CasePage(BaseModel):
    total: int
    total_capped: bool = False   # True = 至少一路 count 触顶，total 为下界
    has_more: bool = False       # 当前页之后是否还有数据（merge 窗口判断）
    total_approx: bool = False   # True = 开了去重等导致 total 仅为近似
    items: list[CaseItem]


class CaseIdsResponse(BaseModel):
    total: int              # 命中总数
    ids: list[str]          # 命中的 task_id（最多 500）
    capped: bool            # total 是否超过 500 上限
