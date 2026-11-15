"""验证码识别结果：事实记录型业务面。

登记（上报）走 app.services.quarantine 引擎：幂等累计连续失败、
达到阈值即隔离代理分配并联动策略降级 / 人工打码队列。
结果行本身不可变更，历史结论保持可解释。
"""
from __future__ import annotations

from typing import Any

from app.services.common import ModuleService
from app.services.quarantine import quarantine_engine
from app.store import store


class CaptcharesultService(ModuleService):
    MODULE = "captcharesult"
    KEY_FIELD = "结果编号"
    ENTRY_LABEL = "验证码识别结果"
    SCOPE_LABEL = "验证码识别结果"

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        entry, _message, missing = quarantine_engine.report(values)
        return entry, missing

    def report(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
        return quarantine_engine.report(values)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if store.find(self.MODULE, entry_id) is None:
            return None, f"{self.ENTRY_LABEL} {entry_id} 不存在或已归档"
        return None, "验证码识别结果是事实记录，不支持动作变更，请上报新的识别结果"
