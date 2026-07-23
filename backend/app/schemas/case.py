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
    exclude_failed: bool = False           # 去除失败任务（超时/报错）

    # Extra dynamic conditions: [{"field": "...", "value": "..."}]
    extra: list[dict] = Field(default_factory=list)


class CaseItem(BaseModel):
    task_id: str
    function_module: str | None = None     # 功能模块
    sub_function: str | None = None        # 二级功能
    question: str | None = None            # 用户提问
    attachment: str | None = None          # 附件
    attachment_url: str | None = None      # 上传的附件链接
    rating: str | None = None              # 好差评
    system_answer: str | None = None       # 系统回答（历史基线）
    is_empty_result: bool | None = None    # 结果是否为空
    stance: str | None = None              # 审查立场
    created_at: str | None = None


class CasePage(BaseModel):
    total: int
    items: list[CaseItem]
