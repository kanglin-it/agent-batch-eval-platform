"""旧版 SaaS 产品页跳转链接（按功能模块拼装）。"""
from urllib.parse import quote

FUXI_DOWNLOAD_TOKEN = "uQrVRghMPLDA8LkG"


def build_baseline_jump_url(
    source: str | None,
    task_id: str,
    *,
    user_id: str | None = None,
    file_id: str | None = None,
    file_name: str | None = None,
) -> str | None:
    """按模块生成旧答案跳转链接；缺关键参数时返回 None。"""
    tid = (task_id or "").strip()
    if not tid or not source:
        return None
    name = (file_name or "").strip()
    uid = (user_id or "").strip()
    fid = (file_id or "").strip()

    if source == "legal_research":
        return f"https://www.zhiexa.com/research/{tid}"

    if source == "document_draft":
        return f"https://www.zhiexa.com/draftChatDetail?taskId={tid}"

    if source == "contract_review":
        if not fid:
            return None
        return (
            f"https://www.zhiexa.com/detail?id={quote(fid, safe='')}"
            f"&tid={quote(tid, safe='')}"
            f"&name={quote(name, safe='')}"
        )

    if source == "file_review":
        return (
            f"https://www.zhiexa.com/fileReviewDetail?taskId={quote(tid, safe='')}"
            f"&name={quote(name, safe='')}"
        )

    if source == "law_ai":
        if not uid:
            return None
        return (
            f"https://fuxi.zhiexa.com/zhiexa/fuxi/api/public/search/ai/law/download/"
            f"{quote(uid, safe='')}/{quote(tid, safe='')}"
            f"?token={FUXI_DOWNLOAD_TOKEN}"
        )

    if source == "case_ai":
        if not uid:
            return None
        return (
            f"https://fuxi.zhiexa.com/zhiexa/fuxi/api/public/search/ai/case/download/"
            f"{quote(uid, safe='')}/{quote(tid, safe='')}"
            f"?token={FUXI_DOWNLOAD_TOKEN}"
        )

    return None
