"""验证码处理业务规则：状态流转、字段校验与筛选口径都收在这里。

通用增查与状态流转继承 app.services.common.ModuleService；
失败驱动的策略降级由 app.services.quarantine 引擎统一决策。
"""
from __future__ import annotations

from app.services.common import ModuleService


class CaptchaService(ModuleService):
    MODULE = "captcha"
    KEY_FIELD = "策略编号"
    ENTRY_LABEL = "验证码策略"
    SCOPE_LABEL = "验证码处理"
    REQUIRED_FIELDS = ["策略编号", "验证码类型", "识别服务商"]
    STATUS_ORDER = ["草稿", "策略验证中", "已启用", "已降级"]
    ACTION_RULES = {"提交策略验证": "策略验证中", "启用策略": "已启用", "降级策略": "已降级"}
    NEGATIVE_ACTIONS: list[str] = []
