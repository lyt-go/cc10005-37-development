"""代理分配结果接口：维护代理实例对目标站点的服务关系。

隔离由失败驱动引擎触发；解除隔离必须先提交一次新的存活检测再标记可用；
回收分配与代理实例回收保持一致，失败组合不会留下可用分配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.proxyassign import ProxyassignService

router = APIRouter(prefix="/api/proxyassign", tags=["代理分配结果"])

service = ProxyassignService()

LIST_FIELDS = ["分配编号", "实例编号", "目标站点", "出站地域", "分配来源", "存活检测时间", "隔离原因", "隔离时间", "隔离到期时间", "解除原因"]
STATUSES = ["待检测", "可用", "隔离中", "已回收"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按分配编号检索"),
    status: str | None = Query(default=None, description="待检测、可用、隔离中、已回收"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按分配编号与状态过滤代理分配列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条代理分配明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"代理分配 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条代理分配，缺字段时说明原因而不是静默丢弃。

    新分配一律从「待检测」起步：通过存活检测后才能标记可用。
    """
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="代理分配已登记，待存活检测后可标记可用", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条代理分配执行提交存活检测、标记可用、回收分配；不满足前置条件时说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出代理分配清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "proxyassign", "total": total, "items": items}
