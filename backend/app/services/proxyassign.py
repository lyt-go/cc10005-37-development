"""代理分配结果：代理实例对目标站点的服务关系。

状态机与隔离解除规则由 app.services.quarantine 引擎统一执行：
隔离到期只转待检测，恢复可用必须有一次新的存活检测；
实例回收由 proxypool 业务面级联，失败组合不会留下可用分配。
"""
from __future__ import annotations

from typing import Any

from app.services.common import ModuleService
from app.services.quarantine import quarantine_engine


class ProxyassignService(ModuleService):
    MODULE = "proxyassign"
    KEY_FIELD = "分配编号"
    ENTRY_LABEL = "代理分配"
    SCOPE_LABEL = "代理分配"
    REQUIRED_FIELDS = ["实例编号", "目标站点", "出站地域"]
    STATUS_ORDER = ["待检测", "可用", "隔离中", "已回收"]

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        quarantine_engine.expire_quarantines()
        return super().list_entries(keyword=keyword, status=status, page=page, size=size)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        quarantine_engine.expire_quarantines()
        return super().get_entry(entry_id)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return quarantine_engine.run_assignment_action(entry_id, action)
