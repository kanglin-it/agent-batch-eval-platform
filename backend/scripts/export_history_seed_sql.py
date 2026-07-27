#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史任务数据集导出脚本 —— 6 个历史数据源 -> INSERT 种子 SQL
==============================================================

- 只读查询源库(PostgreSQL)，把 6 类「历史任务」抽取为测试数据集，生成可导入的 INSERT SQL。
- 每个数据源是一个独立函数(fetch_xxx)，可单独调用/单测(见文件末尾示例)。
- 审查型(合同审查/文件审查)的文件在 OSS 上以密文(.txt)存储；脚本复刻后端
  download_oss_file 的逻辑，下载并 AES 解密到本地明文文件，数据集里存本地路径。

两类结构、两张目标表:

  A. 问答型 -> t_history_qa_dataset (8 列)
     数据源(source = 中文名):
            legal_research (法律研究)  t_legal_research_info  (status='FINISH')
            document_draft (文书起草)  t_document_task        (module='document_draft', task_status='FINISH')
            case_ai        (AI类案)   t_fuxi_history_task    (type='case_ai', status='FINISH')
            law_ai         (AI搜法)   t_fuxi_history_task    (type='law_ai',  status='FINISH')
     字段说明:
       source       数据源标识: legal_research(法律研究) / document_draft(文书起草) / case_ai(AI类案) / law_ai(AI搜法)
       task_id      任务/对话唯一ID (法律研究=chat_id; 其余=task_id)
       question     用户输入的问题
                      法律研究/AI类案/AI搜法 = 原始问题(question / original_question)
                      文书起草 = 经 prompt_id 关联 t_document_prompt.prompt_content
       answer       答案正文
                      法律研究 = llm_answer
                      文书起草 = result, 并按 'zhiexa_reasoning_end' 切掉前面的推理内容
                      AI类案/AI搜法 = t_fuxi_task_output 中 type='analysis' 的 content 按 id 合并
       user_id      用户ID (create_by 或 user_id)
       doc_ids      文件库文件ID列表 (无则 NULL)
       project_id   文件库项目ID (无则 NULL)
       src_created  源记录创建时间 (created / created_at)
     过滤: 均只取已完成(FINISH)且未删除的"主任务"(排除追问/重试子任务)

  B. 审查型 -> t_history_review_dataset (7 列)
     数据源(source = 中文名):
            contract_review (合同审查)  t_contract_tasks    (task_status='FINISH')
            file_review     (文件审查)  t_file_review_task  (task_status='FINISH')
     字段说明:
       source                 数据源标识: contract_review(合同审查) / file_review(文件审查)
       task_id                任务唯一ID
       question               任务名/原文件名 (task_name / origin_name)
       original_file          源文件      (t_file_info: file_type='original_file',         file_version=1)
       detail_annotated_file  最初修订文件 (t_file_info: file_type='detail_annotated_file', file_version=1)
       reference_files        参考文件数组 (t_file_info: file_type='reference_file', 可多个)
       src_created            源记录创建时间 (created)
     文件列取值: 默认(不下载)存 t_file_info.file_url 原始加密URL;
                加 --download / do_download=True 时下载并 AES 解密, 存本地路径
                (本地目录: {DOWNLOAD_DIR}/{task_id}/{file_type}/文件)

这是一个可被 import 调用的公共工具模块, 主要函数:
    query_tasks(source, limit=..., cur=/conn=/dsn=...) -> list[dict]   # 查某个源的历史任务(推荐)
    query_all(limit=..., ...) -> {source: list[dict]}                  # 查全部源
    fetch_<source>(cur, schema, limit[, do_download]) -> (列名, 行)     # 各源底层查询函数, 可单测
    build_conn(dsn=None) -> connection                                 # 建只读连接(优先 dsn, 否则 PG* 环境变量)
    build_insert_sql(source, rows_dicts, schema=, table=) -> str       # 需要时把结果转 INSERT SQL

调用示例:
    from export_history_seed_sql import query_tasks
    # 传连接串, 或先 export PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE
    tasks = query_tasks("legal_research", limit=5, dsn="postgresql://user:pwd@host:5432/db")
    # 审查型可下载解密文件到本地(do_download=True):
    tasks = query_tasks("contract_review", limit=3, do_download=True)

也可直接执行本文件作为 CLI 导出 SQL(python export_history_seed_sql.py -o out.sql --limit 100)。

依赖: psycopg2 (审查型下载解密还需 requests + pycryptodome)
"""
import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

try:
    import psycopg2
    from psycopg2 import extras
except ImportError:
    sys.exit("缺少依赖 psycopg2, 请先: pip install psycopg2-binary")

# ==================== 配置区(按环境修改, 优先读环境变量) ====================
# --- 源库连接: 从环境变量读取, 或调用时用 build_conn(dsn=...) / query_tasks(dsn=...) 传入 ---
#   PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE
DB_HOST = os.getenv("PGHOST", "")
DB_PORT = int(os.getenv("PGPORT", "5432"))
DB_USER = os.getenv("PGUSER", "")
DB_PASSWORD = os.getenv("PGPASSWORD", "")
DB_NAME = os.getenv("PGDATABASE", "")

# --- 文件加解密密钥(与后端 config [crypt] 一致) ---
# 后端两套密钥(不同环境上传的文件可能用不同密钥), 解密时按候选列表依次尝试, 哪套能正确去 padding 就用哪套。
#   A: file_key=55c897adcf75ed57909eb3ca909b7659  iv=1969735b2fcac313063827dc5fcd0cb4  (local_config/PROD)
AES_FILE_KEY = os.getenv("AES_FILE_KEY", "55c897adcf75ed57909eb3ca909b7659")
AES_IV = os.getenv("AES_IV", "1969735b2fcac313063827dc5fcd0cb4")
# 候选密钥(环境变量指定的优先; 去重后依次尝试)
AES_KEY_CANDIDATES = []
for _k, _v in [(AES_FILE_KEY, AES_IV),
               ("55c897adcf75ed57909eb3ca909b7659", "1969735b2fcac313063827dc5fcd0cb4"),
               ("9352cd03eb310dea14b2d43de7e1c188", "8591e40e6f381a9c3ed8c153fa15369b")]:
    if (_k, _v) not in AES_KEY_CANDIDATES:
        AES_KEY_CANDIDATES.append((_k, _v))
# OSS 上以密文存储的对象后缀(见后端 ENCRYPTED_STORAGE_OBJECT_SUFFIXES)
ENCRYPTED_SUFFIXES = (".txt",)
# 审查型文件下载解密后的本地根目录
DOWNLOAD_DIR = os.getenv("HISTORY_DOWNLOAD_DIR", "./history_files")

# 目标表列
QA_COLS = ["source", "task_id", "question", "answer",
           "user_id", "doc_ids", "project_id", "src_created", "result_score"]
REVIEW_COLS = ["source", "task_id", "question",
               "original_file", "detail_annotated_file", "reference_files",
               "stance", "src_created", "result_score"]

# 用户评价(好差评): 关联 t_feedback_record.result_score (0=差评, 1=好评, 2=未知)。
# 关联键: fr.task_id == 各源的任务ID(法律研究=chat_id, 其余=task_id) 且 fr.application 命中该模块。
# 每个 task 至多一条反馈; NULL(无反馈) 与 2(未知) 均视为"无评价", 不参与好差评筛选。
FEEDBACK_APPS = {
    "legal_research": ["legal_research", "library_legal_research"],
    "document_draft": ["document_assistant", "library_document_assistant"],
    "case_ai": ["ai_case", "library_fuxi_search_case_ai"],
    "law_ai": ["ai_law", "library_fuxi_search_law_ai"],
    "contract_review": ["contract_review", "library_contract_review"],
    "file_review": ["file_review"],
}


def _score_sql(schema, source, task_key):
    """返回一段标量子查询 SQL, 取该任务的 result_score(好差评)。task_key 是外层任务ID表达式。"""
    apps = ", ".join("'%s'" % a for a in FEEDBACK_APPS[source])
    return f"""(SELECT fr.result_score
       FROM {schema}.t_feedback_record fr
      WHERE fr.task_id = {task_key} AND fr.is_delete = 0
        AND fr.application IN ({apps})
      LIMIT 1)"""


# ======================= 文件加解密 / 下载 (独立函数) =======================
def aes_cbc_decrypt(ciphertext: bytes, key_hex: str = None, iv_hex: str = None) -> bytes:
    """AES-CBC 解密(复刻后端 AESEncryptDecrypt.decrypt), 返回去 padding 后的明文字节。"""
    from Crypto.Cipher import AES  # pycryptodome, 延迟导入
    from Crypto.Util.Padding import unpad
    key = bytes.fromhex((key_hex or AES_FILE_KEY).replace(" ", ""))
    iv = bytes.fromhex((iv_hex or AES_IV).replace(" ", ""))
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ciphertext), AES.block_size)


def aes_decrypt_any(ciphertext: bytes) -> bytes:
    """依次用 AES_KEY_CANDIDATES 尝试解密, 哪套能正确去 padding 就返回。全失败则抛最后错误。"""
    last_err = None
    for k, v in AES_KEY_CANDIDATES:
        try:
            return aes_cbc_decrypt(ciphertext, k, v)
        except Exception as e:
            last_err = e
    raise last_err if last_err else ValueError("no AES key candidate")


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name or "file")


def download_and_decrypt(url: str, file_name: str, out_dir: Path, timeout: int = 30) -> str:
    """
    下载 OSS 文件到本地; 若对象是 .txt 密文则 AES 解密(复刻后端 download_oss_file)。
    返回本地文件路径。url 为空则返回 None。
    """
    if not url:
        return None
    import requests  # 延迟导入
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    object_name = unquote(url.split("/")[-1].split("?")[0])
    is_txt = object_name.lower().endswith(ENCRYPTED_SUFFIXES)

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()

    fname = _safe_name(file_name or object_name)
    if is_txt:
        # 密文: base64 -> AES 解密(多密钥回退) -> base64 -> 明文字节
        ciphertext = base64.b64decode(resp.content)
        plain_b64 = aes_decrypt_any(ciphertext)
        data = base64.b64decode(plain_b64)
        if fname.lower().endswith(".doc"):
            fname = fname[:-4] + ".docx"
    else:
        data = resp.content

    # 同名去重
    dest = out_dir / fname
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = out_dir / f"{stem}_{i}{suf}"
            i += 1
    dest.write_bytes(data)
    return str(dest)


def _resolve_file(cell, task_id, file_type, do_download):
    """审查型单个文件列: cell 是 {'url':..,'name':..} 的 JSON 字符串或 dict。
    下载解密到 DOWNLOAD_DIR/{task_id}/{file_type}/, 返回本地路径; 不下载则返回原 URL。"""
    if not cell:
        return None
    obj = json.loads(cell) if isinstance(cell, str) else cell
    url, name = obj.get("url"), obj.get("name")
    if not do_download:
        return url
    try:
        return download_and_decrypt(url, name, Path(DOWNLOAD_DIR) / task_id / file_type)
    except Exception as e:
        print(f"[WARN] 下载解密失败 task={task_id} type={file_type} url={url}: {e}", file=sys.stderr)
        return url


def _resolve_files(cell, task_id, file_type, do_download):
    """审查型多文件列(reference_files): cell 是 [{'url','name'},...]。返回本地路径数组 JSON。"""
    if not cell:
        return None
    arr = json.loads(cell) if isinstance(cell, str) else cell
    out = []
    for obj in arr:
        p = _resolve_file(obj, task_id, file_type, do_download)
        if p:
            out.append(p)
    return json.dumps(out, ensure_ascii=False) if out else None


# ======================= 各数据源查询(独立可测函数) =======================
def _limit(limit):
    return "" if not limit or limit <= 0 else f"LIMIT {int(limit)}"


def fetch_legal_research(cur, schema="public", limit=100):
    """1) 法律研究 -> QA_COLS"""
    cur.execute(f"""
        SELECT 'legal_research' AS source, t.chat_id AS task_id,
               t.question AS question, t.llm_answer AS answer,
               t.create_by AS user_id, t.doc_ids AS doc_ids, t.project_id AS project_id,
               t.created AS src_created,
               {_score_sql(schema, 'legal_research', 't.chat_id')} AS result_score
        FROM {schema}.t_legal_research_info t
        WHERE t.is_delete = 0 AND t.status = 'FINISH'
          AND (t.parent_chat_id IS NULL OR t.parent_chat_id = t.chat_id)
        ORDER BY t.created DESC {_limit(limit)}
    """)
    return QA_COLS, cur.fetchall()


def fetch_document_draft(cur, schema="public", limit=100):
    """2) 文书起草 -> QA_COLS; 答案按 'zhiexa_reasoning_end' 切掉推理内容; 主任务 parent_task_id=task_id

    传统文书: doc_generation_mode='document_assistant'
    要素式:   module='document_draft' AND mode<>'document_assistant'
    """
    cur.execute(f"""
        WITH ids AS (
            SELECT t.task_id, t.created
            FROM {schema}.t_document_task t
            WHERE t.is_delete = 0
              AND (
                    t.doc_generation_mode = 'document_assistant'
                 OR (t.module = 'document_draft'
                     AND COALESCE(t.doc_generation_mode, '') <> 'document_assistant')
              )
              AND t.task_status = 'FINISH'
              AND t.parent_task_id = t.task_id
            ORDER BY t.created DESC {_limit(limit)}
        )
        SELECT 'document_draft' AS source, ids.task_id AS task_id,
               p.prompt_content AS question,
               NULLIF(btrim(regexp_replace(t.result, '^.*?zhiexa_reasoning_end', '', 's')), '') AS answer,
               t.create_by AS user_id, t.doc_ids AS doc_ids, t.project_id AS project_id,
               ids.created AS src_created,
               {_score_sql(schema, 'document_draft', 'ids.task_id')} AS result_score
        FROM ids
        JOIN {schema}.t_document_task t        ON t.task_id = ids.task_id
        LEFT JOIN {schema}.t_document_prompt p ON p.prompt_id = t.prompt_id
        ORDER BY ids.created DESC
    """)
    return QA_COLS, cur.fetchall()


def _fetch_fuxi(cur, schema, limit, task_type):
    """3)/4) AI类案 / AI搜法 -> QA_COLS; 答案合并 t_fuxi_task_output(type='analysis')"""
    cur.execute(f"""
        SELECT %(src)s AS source, h.task_id AS task_id,
               h.original_question AS question,
               (SELECT string_agg(o.content, '' ORDER BY o.id)
                  FROM {schema}.t_fuxi_task_output o
                 WHERE o.task_id = h.task_id AND o.type = 'analysis') AS answer,
               h.user_id AS user_id, h.doc_ids AS doc_ids, h.project_id AS project_id,
               h.created_at AS src_created,
               {_score_sql(schema, task_type, 'h.task_id')} AS result_score
        FROM {schema}.t_fuxi_history_task h
        WHERE h.is_deleted = 0 AND h.type = %(t)s
          AND h.parent_task_id IS NULL AND h.status = 'FINISH'
        ORDER BY h.created_at DESC {_limit(limit)}
    """, {"src": task_type, "t": task_type})
    return QA_COLS, cur.fetchall()


def fetch_case_ai(cur, schema="public", limit=100):
    return _fetch_fuxi(cur, schema, limit, "case_ai")


def fetch_law_ai(cur, schema="public", limit=100):
    return _fetch_fuxi(cur, schema, limit, "law_ai")


# 审查型: 查询返回 {url,name} 便于脚本下载解密到本地
_REVIEW_FILE_SQL = """
    (SELECT jsonb_build_object('url', f.file_url, 'name', f.file_name)
       FROM {schema}.t_file_info f
      WHERE f.task_id = t.task_id AND f.is_delete = 0
        AND f.file_type = '{ftype}' AND f.file_version = 1
      ORDER BY f.created DESC LIMIT 1)"""

_REVIEW_REFFILES_SQL = """
    (SELECT jsonb_agg(jsonb_build_object('url', f.file_url, 'name', f.file_name) ORDER BY f.created)
       FROM {schema}.t_file_info f
      WHERE f.task_id = t.task_id AND f.is_delete = 0
        AND f.file_type = 'reference_file')"""


def _contract_stance_sql(schema):
    """合同审查持方页(审查立场)信息 -> jsonb。
    t_contract_tasks.contract_id -> t_contract_meta 取 review_stance/review_people/
    review_status/custom_require; subject 由 t_contract_review_info 按 task_id + 相同
    review_stance 关联得到(立场对应的主体名称)。"""
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


def _file_stance_sql(schema):
    """文件审查持方页: custom_require 即为持方页内容。"""
    return "jsonb_build_object('custom_require', t.custom_require)"


def _fetch_review(cur, schema, limit, source, table, name_expr, stance_expr, do_download):
    orig = _REVIEW_FILE_SQL.format(schema=schema, ftype="original_file")
    detail = _REVIEW_FILE_SQL.format(schema=schema, ftype="detail_annotated_file")
    refs = _REVIEW_REFFILES_SQL.format(schema=schema)
    cur.execute(f"""
        SELECT %(src)s AS source, t.task_id AS task_id, {name_expr} AS question,
               {orig} AS original_file,
               {detail} AS detail_annotated_file,
               {refs} AS reference_files,
               {stance_expr} AS stance,
               t.created AS src_created,
               {_score_sql(schema, source, 't.task_id')} AS result_score
        FROM {schema}.{table} t
        WHERE t.is_delete = 0 AND t.task_status = 'FINISH'
        ORDER BY t.created DESC {_limit(limit)}
    """, {"src": source})
    rows = []
    for r in cur.fetchall():
        r = list(r)
        tid = r[1]
        r[3] = _resolve_file(r[3], tid, "original_file", do_download)  # -> {task_id}/original_file/
        r[4] = _resolve_file(r[4], tid, "detail_annotated_file", do_download)  # -> {task_id}/detail_annotated_file/
        r[5] = _resolve_files(r[5], tid, "reference_file", do_download)  # -> {task_id}/reference_file/
        rows.append(tuple(r))
    return REVIEW_COLS, rows


def fetch_contract_review(cur, schema="public", limit=100, do_download=False):
    """5) 合同审查 -> REVIEW_COLS (stance 关联 t_contract_meta / t_contract_review_info)"""
    return _fetch_review(cur, schema, limit, "contract_review",
                         "t_contract_tasks", "t.task_name",
                         _contract_stance_sql(schema), do_download)


def fetch_file_review(cur, schema="public", limit=100, do_download=False):
    """6) 文件审查 -> REVIEW_COLS (stance = custom_require)"""
    return _fetch_review(cur, schema, limit, "file_review",
                         "t_file_review_task", "COALESCE(t.origin_name, t.task_name)",
                         _file_stance_sql(schema), do_download)


SOURCES = {
    "legal_research": {"label": "法律研究", "kind": "qa", "fn": fetch_legal_research},
    "document_draft": {"label": "文书起草", "kind": "qa", "fn": fetch_document_draft},
    "case_ai": {"label": "AI类案", "kind": "qa", "fn": fetch_case_ai},
    "law_ai": {"label": "AI搜法", "kind": "qa", "fn": fetch_law_ai},
    "contract_review": {"label": "合同审查", "kind": "review", "fn": fetch_contract_review},
    "file_review": {"label": "文件审查", "kind": "review", "fn": fetch_file_review},
}
SOURCE_ORDER = ["legal_research", "document_draft", "case_ai", "law_ai",
                "contract_review", "file_review"]

QA_DDL = """\
-- 问答型历史任务数据集(法律研究/文书起草/AI类案/AI搜法)
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    id           bigserial PRIMARY KEY,
    source       varchar(32)  NOT NULL,
    task_id      varchar(128) NOT NULL,
    question     text,
    answer       text,
    user_id      varchar(128),
    doc_ids      text,
    project_id   varchar(128),
    src_created  timestamptz,
    result_score smallint,     -- 用户评价: 0=差评,1=好评,2=未知; NULL=无反馈
    CONSTRAINT uq_{table}_source_task UNIQUE (source, task_id)
);
"""

REVIEW_DDL = """\
-- 审查型历史任务数据集(合同审查/文件审查); 文件列存下载解密后的本地路径
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    id                    bigserial PRIMARY KEY,
    source                varchar(32)  NOT NULL,
    task_id               varchar(128) NOT NULL,
    question              text,
    original_file         text,     -- 源文件(本地解密后路径)
    detail_annotated_file text,     -- 最初修订文件(本地解密后路径)
    reference_files       jsonb,    -- 参考文件本地路径数组
    stance                jsonb,    -- 审查立场/持方页信息(合同审查=review_stance/subject/review_people/review_status/custom_require; 文件审查=custom_require)
    src_created           timestamptz,
    result_score          smallint, -- 用户评价: 0=差评,1=好评,2=未知; NULL=无反馈
    CONSTRAINT uq_{table}_source_task UNIQUE (source, task_id)
);
"""


def build_conn(dsn=None):
    """建立源库只读连接。优先 dsn, 否则用 PG* 环境变量。"""
    if dsn:
        conn = psycopg2.connect(dsn)
    else:
        if not DB_HOST:
            raise RuntimeError(
                "未配置数据库连接: 请设置 PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE 环境变量, "
                "或调用时传 dsn=\"postgresql://user:pwd@host:5432/db\"")
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER,
            password=DB_PASSWORD, dbname=DB_NAME,
        )
    conn.set_session(readonly=True, autocommit=True)
    extras.register_default_json(loads=lambda x: x, globally=True)
    extras.register_default_jsonb(loads=lambda x: x, globally=True)
    return conn


def query_tasks(source, limit=100, *, cur=None, conn=None, dsn=None,
                schema="public", do_download=False):
    """
    公共入口: 查询某个历史数据源的任务, 返回 list[dict](每行一个 dict, 列名->值)。

    连接优先级: 传入的 cur > 传入的 conn > 用 dsn/环境变量新建(用完自动关闭)。
    source: legal_research/document_draft/case_ai/law_ai/contract_review/file_review
    do_download: 仅审查型有效, True 时下载并解密文件到本地(见 DOWNLOAD_DIR)。
    """
    if source not in SOURCES:
        raise ValueError(f"未知数据源: {source}; 可选: {SOURCE_ORDER}")
    own_conn = None
    if cur is None:
        if conn is None:
            own_conn = conn = build_conn(dsn)
        cur = conn.cursor()
    try:
        meta = SOURCES[source]
        if meta["kind"] == "review":
            cols, rows = meta["fn"](cur, schema, limit, do_download=do_download)
        else:
            cols, rows = meta["fn"](cur, schema, limit)
        return [dict(zip(cols, r)) for r in rows]
    finally:
        if own_conn is not None:
            own_conn.close()


def query_all(limit=100, *, cur=None, conn=None, dsn=None, schema="public",
              do_download=False, sources=None):
    """查询多个源, 返回 {source: list[dict]}。sources 默认全部 6 个。"""
    keys = sources or SOURCE_ORDER
    own_conn = None
    if cur is None:
        if conn is None:
            own_conn = conn = build_conn(dsn)
        cur = conn.cursor()
    try:
        return {k: query_tasks(k, limit, cur=cur, schema=schema, do_download=do_download)
                for k in keys}
    finally:
        if own_conn is not None:
            own_conn.close()


def build_insert_sql(source, rows_dicts, schema="public", table=None, batch_size=100):
    """把 query_tasks 返回的 list[dict] 转成 INSERT SQL 字符串(需要落库时用)。"""
    if source not in SOURCES:
        raise ValueError(f"未知数据源: {source}")
    kind = SOURCES[source]["kind"]
    target_cols = QA_COLS if kind == "qa" else REVIEW_COLS
    if table is None:
        table = "t_history_qa_dataset" if kind == "qa" else "t_history_review_dataset"
    rows = [tuple(d.get(c) for c in target_cols) for d in rows_dicts]
    conn = build_conn()
    try:
        cur = conn.cursor()
        return "\n".join(rows_to_insert_sql(cur, rows, schema, table, target_cols, batch_size))
    finally:
        conn.close()


def rows_to_insert_sql(cur, rows, schema, table, target_cols, batch_size=100):
    """把行集转成分批的 INSERT ... ON CONFLICT DO NOTHING 语句列表。"""
    if not rows:
        return []
    header = f"INSERT INTO {schema}.{table} ({', '.join(target_cols)}) VALUES"
    tail = "ON CONFLICT (source, task_id) DO NOTHING;"
    statements = []
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        lines = ["    " + cur.mogrify("(" + ", ".join(["%s"] * len(r)) + ")", tuple(r)).decode("utf-8")
                 for r in chunk]
        statements.append(header + "\n" + ",\n".join(lines) + "\n" + tail)
    return statements


def main():
    parser = argparse.ArgumentParser(description="导出 6 个历史数据源为 INSERT 种子 SQL(只查询源库)")
    parser.add_argument("-o", "--output", default="history_seed.sql")
    parser.add_argument("--dsn", default=None, help="postgresql://user:pwd@host:port/db; 不给则读 PG* 环境变量")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--qa-table", default="t_history_qa_dataset")
    parser.add_argument("--review-table", default="t_history_review_dataset")
    parser.add_argument("--sources", default=",".join(SOURCE_ORDER),
                        help="逗号分隔; 可选: " + ",".join(SOURCE_ORDER))
    parser.add_argument("--limit", type=int, default=3, help="每源上限(0=全量)")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--download", action="store_true",
                        help="审查型下载并解密文件到本地(默认不下载, 只存原始加密 URL)")
    parser.add_argument("--no-ddl", action="store_true")
    args = parser.parse_args()

    selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in selected if s not in SOURCES]
    if unknown:
        sys.exit(f"未知数据源: {unknown}; 可选: {SOURCE_ORDER}")

    schema = args.schema
    conn = build_conn(args.dsn)
    cur = conn.cursor()

    parts = [
        "-- 历史任务数据集种子 SQL",
        f"-- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"-- 数据源: {', '.join(selected)}",
        f"-- 目标表: {schema}.{args.qa_table}(问答型) / {schema}.{args.review_table}(审查型)",
        "",
    ]
    if not args.no_ddl:
        if any(SOURCES[k]["kind"] == "qa" for k in selected):
            parts.append(QA_DDL.format(schema=schema, table=args.qa_table))
        if any(SOURCES[k]["kind"] == "review" for k in selected):
            parts.append(REVIEW_DDL.format(schema=schema, table=args.review_table))

    stats = []
    for key in SOURCE_ORDER:
        if key not in selected:
            continue
        meta = SOURCES[key]
        kind, label, fn = meta["kind"], meta["label"], meta["fn"]
        table = args.qa_table if kind == "qa" else args.review_table
        target_cols = QA_COLS if kind == "qa" else REVIEW_COLS
        try:
            if kind == "review":
                _, rows = fn(cur, schema, args.limit, do_download=args.download)
            else:
                _, rows = fn(cur, schema, args.limit)
        except Exception as e:
            print(f"[WARN] 数据源 {key}({label}) 处理失败, 已跳过: {e}", file=sys.stderr)
            stats.append((label, key, "ERROR"))
            parts.append(f"-- [{key}] {label}: 处理失败, 已跳过 ({e})\n")
            continue

        stats.append((label, key, len(rows)))
        parts.append(f"-- ============ [{key}] {label} : {len(rows)} 条 -> {table} ============")
        stmts = rows_to_insert_sql(cur, rows, schema, table, target_cols, args.batch_size)
        parts.extend(stmts if stmts else ["-- (无数据)"])
        parts.append("")

    cur.close()
    conn.close()

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    print("=" * 52)
    print(f"已写出: {args.output}   (审查型文件下载至: {DOWNLOAD_DIR})")
    print(f"每源上限 limit = {args.limit if args.limit > 0 else '全量'}")
    print("-" * 52)
    print(f"{'数据源':<16}{'source':<18}{'条数':>8}")
    for label, key, n in stats:
        print(f"{label:<16}{key:<18}{str(n):>8}")
    print("=" * 52)


if __name__ == "__main__":
    main()

# ============================ 单函数测试示例 ============================
# 只测某个源的 SQL(不生成文件):
#   from export_history_seed_sql import build_conn, fetch_legal_research
#   conn = build_conn(); cur = conn.cursor()
#   cols, rows = fetch_legal_research(cur, "public", 5); print(cols); print(rows[:2])
# 测审查型(下载解密到本地 ./history_files/<task_id>/):
#   cols, rows = fetch_contract_review(cur, "public", 3, do_download=True)
# 只测解密函数:
#   from export_history_seed_sql import download_and_decrypt
#   print(download_and_decrypt("<oss_url>.txt", "合同.docx", "./tmp"))
