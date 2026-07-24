"""Diagnose QA file resolution end-to-end for one case.

Given a task_id from a QA case that wrongly shows 有附件/下载文件, this prints:
  - the configured library service
  - the case's raw doc_ids / project_id from the DB
  - the exact request sent to the public query API and its RAW response
  - the final resolved [{url, name}] list

Usage:
    cd backend && python -m scripts.diag_library <task_id>
"""
import asyncio
import json
import sys

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.db.case_session import CaseSessionLocal
from app.services.case_source import build_hydrate_sql
from app.services.library_client import _QUERY_PATH, _parse_doc_ids, resolve_oss_urls


async def main(task_id: str) -> None:
    print("=" * 60)
    print("library_service =", repr(settings.library_service))
    print("library_timeout =", settings.library_timeout)
    print("case_schema     =", settings.case_schema)
    print("=" * 60)

    async with CaseSessionLocal() as db:
        sql = text(build_hydrate_sql(schema=settings.case_schema))
        rows = (await db.execute(sql, {"ids": [task_id]})).mappings().all()

    if not rows:
        print(f"!! no case found for task_id={task_id!r} (check the id / schema)")
        return

    r = dict(rows[0])
    # For QA rows the hydrate SQL carries doc_ids in the `attachment` column.
    doc_ids = r.get("attachment")
    project_id = r.get("project_id")
    print("kind       =", r.get("kind"))
    print("source     =", r.get("source"))
    print("doc_ids    =", repr(doc_ids))
    print("project_id =", repr(project_id))
    print("parsed file_ids =", _parse_doc_ids(doc_ids))
    print("=" * 60)

    if not settings.library_service:
        print("!! library_service is empty — set [library] service in config.ini")
        return

    # Fire the same requests resolve_oss_urls would, printing RAW responses.
    payloads = []
    fids = _parse_doc_ids(doc_ids)
    if fids:
        payloads.append({"file_ids": fids})
    if project_id:
        payloads.append({"project_id": project_id})
    if not payloads:
        print("!! nothing to resolve (no doc_ids and no project_id)")
        return

    url = f"{settings.library_service}{_QUERY_PATH}"
    async with httpx.AsyncClient(timeout=settings.library_timeout) as client:
        for p in payloads:
            print(f"POST {url}")
            print("  request :", json.dumps(p, ensure_ascii=False))
            try:
                resp = await client.post(url, json=p,
                                         headers={"Content-Type": "application/json"})
                print("  status  :", resp.status_code)
                body = resp.json()
                print("  response:", json.dumps(body, ensure_ascii=False)[:2000])
            except Exception as exc:  # noqa: BLE001
                print("  ERROR   :", repr(exc))
            print("-" * 60)

    resolved = await resolve_oss_urls(project_id, doc_ids)
    print("resolve_oss_urls ->", json.dumps(resolved, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.diag_library <task_id>")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1]))
