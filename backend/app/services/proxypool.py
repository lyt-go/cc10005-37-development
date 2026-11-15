"""代理池管理业务规则：状态流转、字段校验与筛选口径都收在这里。

通用增查与状态流转继承 app.services.common.ModuleService；
回收实例时通过 app.services.quarantine 引擎同步回收全部分配，
保证代理回收与验证码策略降级的结论一致。
"""
from __future__ import annotations

from typing import Any

from app.services.common import ModuleService
from app.services.quarantine import quarantine_engine


class ProxypoolService(ModuleService):
    MODULE = "proxypool"
    KEY_FIELD = "实例编号"
    ENTRY_LABEL = "代理实例"
    SCOPE_LABEL = "代理池管理"
    REQUIRED_FIELDS = ["实例编号", "代理地址", "代理端口"]
    STATUS_ORDER = ["草稿", "存活检测中", "可用", "已回收"]
    ACTION_RULES = {"提交存活检测": "存活检测中", "标记可用": "可用", "回收实例": "已回收"}
    NEGATIVE_ACTIONS: list[str] = []

    def after_action(self, entry: dict[str, Any], action: str) -> list[str]:
        if action == "回收实例":
            return quarantine_engine.cascade_proxy_recycled(str(entry.get("实例编号", "")))
        return []
