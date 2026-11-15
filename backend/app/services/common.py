"""业务模块通用骨架：列表筛选、登记校验、状态机流转的统一写法。

验证码处理、代理池管理、验证码识别结果、代理分配结果等业务面共用这一份实现，
模块之间的差异只通过类属性声明（编号字段、必填字段、状态序列、动作表），
不再每个模块复制一份 list/get/create/run_action。
"""
from __future__ import annotations

from typing import Any

from app.store import store


class ModuleService:
    """一个业务模块的通用增查与状态流转。

    子类只需要声明类属性；个别模块有特殊规则时，重写对应钩子即可。
    """

    MODULE: str = ""                 # store 里的表名
    KEY_FIELD: str = ""              # 编号字段，关键字检索按它过滤
    ENTRY_LABEL: str = "记录"         # 报错与提示文案里的称呼
    SCOPE_LABEL: str = ""            # 「可执行范围」文案里的业务面名称
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS: list[str] = []

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(self.MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(self.KEY_FIELD, ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in self.REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(self.MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in self.REQUIRED_FIELDS})
        entry["status"] = self.STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTRY_LABEL} {entry_id} 不存在或已归档"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.SCOPE_LABEL}可执行范围"
        target = self.ACTION_RULES[action]
        if target not in self.STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != self.STATUS_ORDER[-1]
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        notes = self.after_action(entry, action)
        message = f"{self.ENTRY_LABEL}已{action}"
        if notes:
            message = f"{message}；{'；'.join(notes)}"
        return entry, message

    def after_action(self, entry: dict[str, Any], action: str) -> list[str]:
        """动作生效后的级联钩子：返回需要追加到提示文案里的说明。"""
        return []
