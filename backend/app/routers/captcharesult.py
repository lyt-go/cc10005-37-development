"""验证码识别结果接口：上报识别结果，查询连续失败与处置结论。

上报走失败驱动隔离引擎：重复上报单号只累计一次；达到阈值会把代理分配隔离、
策略降级并把识别结果转入人工打码队列。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.captcharesult import CaptcharesultService

router = APIRouter(prefix="/api/captcharesult", tags=["验证码识别结果"])

service = CaptcharesultService()

LIST_FIELDS = ["结果编号", "策略编号", "验证码类型", "识别服务商", "实例编号", "目标站点", "识别结果", "连续失败数", "失败原因", "上报单号", "上报时间", "关联分配", "处置说明"]
STATUSES = ["已记录", "人工打码中", "已降级"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按结果编号检索"),
    status: str | None = Query(default=None, description="已记录、人工打码中、已降级"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结果编号与状态过滤识别结果列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条验证码识别结果明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"验证码识别结果 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def report_entry(payload: EntryPayload) -> ActionResult:
    """上报一条识别结果：成功清零计数，失败累计连续失败并按阈值触发隔离。

    同一上报单号重复提交只累计一次；触发隔离时返回的文案会说明
    隔离的分配、降级的策略、识别结果去向与接管站点的故障转移分配。
    """
    entry, message, missing = service.report(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """识别结果是事实记录，不支持动作变更；该入口只返回可读说明。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出验证码识别结果清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "captcharesult", "total": total, "items": items}
