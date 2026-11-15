"""失败驱动的代理隔离引擎：验证码处理与代理池管理共用的决策内核。

两个业务面都通过这里汇总连续失败、执行隔离、切换地域、转入人工打码，
保证「验证码识别结果」和「代理分配结果」看到的是同一份结论：

- 连续失败按（验证码类型、识别服务商、代理实例、目标站点）汇总，达到阈值即隔离分配；
- 重复上报按上报单号幂等，只累计一次；
- 隔离后策略降级、识别结果按手动打码开关进入人工队列或随策略降级关闭；
- 人工处理期间按地域切换顺序启用其他可用代理继续服务目标站点；
- 隔离到期只转「待检测」，解除隔离必须有一次新的存活检测；
- 代理实例回收时同步回收全部分配，失败组合不得留下可用分配。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.store import store

# —— 阈值与节奏（按当前业务量级定的初始值，需要调整时只改这里）——
FAILURE_THRESHOLD = 3                                # 同一组合连续失败达到该次数即隔离代理分配
QUARANTINE_MINUTES = 30                              # 隔离时长：到期转待检测，不会自动恢复可用
REGION_FAILOVER_ORDER = ["华东", "华北", "华南", "境外"]  # 地域切换顺序：从当前地域的下一个开始找

RESULT_MODULE = "captcharesult"     # 验证码识别结果
ASSIGN_MODULE = "proxyassign"       # 代理分配结果
STRATEGY_MODULE = "captcha"         # 验证码策略
PROXY_MODULE = "proxypool"          # 代理实例

ASSIGN_SERVING = "可用"
ASSIGN_QUARANTINED = "隔离中"
ASSIGN_CHECKING = "待检测"
ASSIGN_RECYCLED = "已回收"

RESULT_SUCCESS = "成功"
RESULT_FAILURE = "识别失败"
RESULT_RECORDED = "已记录"
RESULT_MANUAL = "人工打码中"
RESULT_DEGRADED = "已降级"

STRATEGY_DEGRADED = "已降级"

REQUIRED_REPORT_FIELDS = ["策略编号", "验证码类型", "识别服务商", "实例编号", "目标站点", "识别结果", "上报单号"]

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _next_code(rows: list[dict[str, Any]], field: str, prefix: str) -> str:
    """按带连字符的前缀（如 CAPR-）生成下一个编号。"""
    numbers: list[int] = []
    for row in rows:
        code = str(row.get(field, ""))
        if code.startswith(prefix):
            try:
                numbers.append(int(code[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def _find_by_code(module: str, field: str, code: str) -> dict[str, Any] | None:
    for row in store.rows(module):
        if str(row.get(field, "")) == code:
            return row
    return None


class QuarantineEngine:
    """连续失败汇总与隔离决策：captcha / proxypool 两个业务面共用。"""

    # ---------- 验证码识别结果上报 ----------

    def report(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
        """登记一条识别结果；失败时累计连续失败并按阈值触发隔离。

        返回（结果行， 提示文案， 缺失字段）；同一上报单号只累计一次。
        """
        missing = [field for field in REQUIRED_REPORT_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, "", missing

        report_no = str(values["上报单号"]).strip()
        existing = _find_by_code(RESULT_MODULE, "上报单号", report_no)
        if existing is not None:
            return existing, f"上报单号 {report_no} 已记录过，重复失败只累计一次，本次不再计数", []

        result_kind = str(values["识别结果"]).strip()
        if result_kind not in (RESULT_SUCCESS, RESULT_FAILURE):
            return None, f"识别结果只接受「{RESULT_SUCCESS}」或「{RESULT_FAILURE}」", []
        if result_kind == RESULT_FAILURE and not str(values.get("失败原因") or "").strip():
            return None, "", ["失败原因"]

        strategy = _find_by_code(STRATEGY_MODULE, "策略编号", str(values["策略编号"]).strip())
        if strategy is None:
            return None, f"验证码策略 {values['策略编号']} 不存在或已归档", []
        proxy = _find_by_code(PROXY_MODULE, "实例编号", str(values["实例编号"]).strip())
        if proxy is None:
            return None, f"代理实例 {values['实例编号']} 不存在或已回收", []
        if str(strategy.get("验证码类型", "")) != str(values["验证码类型"]).strip():
            return None, f"上报的验证码类型与策略 {strategy['策略编号']} 登记的「{strategy.get('验证码类型')}」不一致", []
        if str(strategy.get("识别服务商", "")) != str(values["识别服务商"]).strip():
            return None, f"上报的识别服务商与策略 {strategy['策略编号']} 登记的「{strategy.get('识别服务商')}」不一致", []

        self.expire_quarantines()

        key = self._failure_key(values)
        success = result_kind == RESULT_SUCCESS
        count = 0 if success else self._latest_count(key) + 1

        rows = store.rows(RESULT_MODULE)
        now = datetime.now()
        row: dict[str, Any] = {
            "id": max((int(item.get("id", 0)) for item in rows), default=0) + 1,
            "结果编号": _next_code(rows, "结果编号", "CAPR-"),
            "策略编号": strategy["策略编号"],
            "验证码类型": str(values["验证码类型"]).strip(),
            "识别服务商": str(values["识别服务商"]).strip(),
            "实例编号": proxy["实例编号"],
            "目标站点": str(values["目标站点"]).strip(),
            "识别结果": result_kind,
            "连续失败数": count,
            "失败原因": str(values.get("失败原因") or "").strip() or "—",
            "上报单号": report_no,
            "上报时间": _fmt(now),
            "关联分配": "",
            "处置说明": "",
            "status": RESULT_RECORDED,
            "pending": False,
            "abnormal": not success,
        }
        rows.append(row)

        if success:
            return row, "识别成功已记录，该组合连续失败计数清零", []

        notes = [f"识别失败已记录，该组合连续失败 {count} 次"]
        if count >= FAILURE_THRESHOLD:
            notes += self._isolate_assignment(key, row, now)
            notes += self._degrade_strategy(strategy, key, count, now)
            notes += self._route_result(strategy, row)
        self.enforce_consistency()
        return row, "；".join(notes), []

    # ---------- 代理分配动作 ----------

    def run_assignment_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(ASSIGN_MODULE, entry_id)
        if entry is None:
            return None, f"代理分配 {entry_id} 不存在或已归档"
        self.expire_quarantines()
        code = entry.get("分配编号", entry_id)
        now = datetime.now()

        if action == "提交存活检测":
            if entry["status"] == ASSIGN_RECYCLED:
                return None, f"分配 {code} 已回收，不能再提交存活检测"
            if entry["status"] == ASSIGN_SERVING:
                return None, f"分配 {code} 当前可用，无需重复提交存活检测"
            entry["status"] = ASSIGN_CHECKING
            entry["pending"] = True
            entry["存活检测时间"] = _fmt(now)
            return entry, f"分配 {code} 已重新提交存活检测，等待检测结论"

        if action == "标记可用":
            if entry["status"] == ASSIGN_SERVING:
                return entry, f"分配 {code} 已处于可用状态"
            if entry["status"] == ASSIGN_RECYCLED:
                return None, f"分配 {code} 已回收，不能恢复可用"
            if entry["status"] == ASSIGN_QUARANTINED:
                return None, f"分配 {code} 仍在隔离中，须先提交一次新的存活检测"
            quarantined_at = _parse(entry.get("隔离时间"))
            checked_at = _parse(entry.get("存活检测时间"))
            # 时间戳精确到秒：检测时间早于隔离时间才判定为「旧检测」，
            # 同一秒内的复检视为隔离之后的新检测。
            if quarantined_at is not None and (checked_at is None or checked_at < quarantined_at):
                return None, f"分配 {code} 解除隔离需要一次新的存活检测：当前检测时间早于隔离时间"
            entry["status"] = ASSIGN_SERVING
            entry["pending"] = False
            entry["abnormal"] = False
            if quarantined_at is not None:
                entry["解除原因"] = "新存活检测通过，隔离解除"
            return entry, f"分配 {code} 已通过新的存活检测，恢复可用"

        if action == "回收分配":
            if entry["status"] == ASSIGN_RECYCLED:
                return None, f"分配 {code} 已回收，请勿重复操作"
            entry["status"] = ASSIGN_RECYCLED
            entry["pending"] = False
            entry["解除原因"] = "分配人工回收"
            self.enforce_consistency()
            return entry, f"分配 {code} 已回收"

        return None, f"动作「{action}」不属于代理分配可执行范围"

    # ---------- 代理实例级联 ----------

    def cascade_proxy_recycled(self, proxy_code: str) -> list[str]:
        """代理实例回收时同步回收它的全部分配，保证回收与降级结论一致。"""
        notes: list[str] = []
        for row in store.rows(ASSIGN_MODULE):
            if str(row.get("实例编号", "")) != proxy_code or row.get("status") == ASSIGN_RECYCLED:
                continue
            previous = row.get("status", "")
            row["status"] = ASSIGN_RECYCLED
            row["pending"] = False
            row["解除原因"] = "代理实例回收，分配同步回收"
            notes.append(f"分配 {row.get('分配编号')}（原{previous}）已同步回收")
        self.enforce_consistency()
        return notes

    # ---------- 一致性兜底 ----------

    def expire_quarantines(self) -> None:
        """隔离到期只转「待检测」：解除隔离必须再过一次新的存活检测。"""
        now = datetime.now()
        for row in store.rows(ASSIGN_MODULE):
            if row.get("status") != ASSIGN_QUARANTINED:
                continue
            deadline = _parse(row.get("隔离到期时间"))
            if deadline is not None and deadline <= now:
                row["status"] = ASSIGN_CHECKING
                row["pending"] = True
                row["解除原因"] = "隔离到期，需重新存活检测"

    def enforce_consistency(self) -> None:
        """两条硬约束：已回收实例不留分配；隔离中的组合不留可用分配。"""
        recycled = {
            str(row.get("实例编号", ""))
            for row in store.rows(PROXY_MODULE)
            if row.get("status") == "已回收"
        }
        assignments = store.rows(ASSIGN_MODULE)
        for row in assignments:
            if str(row.get("实例编号", "")) in recycled and row.get("status") != ASSIGN_RECYCLED:
                row["status"] = ASSIGN_RECYCLED
                row["pending"] = False
                row["解除原因"] = "代理实例已回收，分配一致性回收"
        quarantined_pairs = {
            (str(row.get("实例编号", "")), str(row.get("目标站点", "")))
            for row in assignments
            if row.get("status") == ASSIGN_QUARANTINED
        }
        for row in assignments:
            pair = (str(row.get("实例编号", "")), str(row.get("目标站点", "")))
            if pair in quarantined_pairs and row.get("status") == ASSIGN_SERVING:
                row["status"] = ASSIGN_CHECKING
                row["pending"] = True
                row["解除原因"] = "同组合存在隔离中的分配，可用结论收回待检测"

    # ---------- 内部：失败汇总与隔离决策 ----------

    def _failure_key(self, values: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(values.get("验证码类型", "")).strip(),
            str(values.get("识别服务商", "")).strip(),
            str(values.get("实例编号", "")).strip(),
            str(values.get("目标站点", "")).strip(),
        )

    def _latest_count(self, key: tuple[str, str, str, str]) -> int:
        count = 0
        for row in store.rows(RESULT_MODULE):
            row_key = (
                str(row.get("验证码类型", "")),
                str(row.get("识别服务商", "")),
                str(row.get("实例编号", "")),
                str(row.get("目标站点", "")),
            )
            if row_key == key:
                count = int(row.get("连续失败数", 0))
        return count

    def _active_assignment(self, proxy_code: str, site: str) -> dict[str, Any] | None:
        for row in store.rows(ASSIGN_MODULE):
            if (
                str(row.get("实例编号", "")) == proxy_code
                and str(row.get("目标站点", "")) == site
                and row.get("status") == ASSIGN_SERVING
            ):
                return row
        return None

    def _isolate_assignment(self, key: tuple[str, str, str, str], result: dict[str, Any], now: datetime) -> list[str]:
        captcha_type, provider, proxy_code, site = key
        count = int(result.get("连续失败数", 0))
        assignment = self._active_assignment(proxy_code, site)
        if assignment is None:
            for row in store.rows(ASSIGN_MODULE):
                if (
                    str(row.get("实例编号", "")) == proxy_code
                    and str(row.get("目标站点", "")) == site
                    and row.get("status") == ASSIGN_QUARANTINED
                ):
                    result["关联分配"] = row.get("分配编号", "")
                    return [f"分配 {row.get('分配编号')} 已处于隔离中，维持隔离不重复处置"]
            return [f"{proxy_code} 对 {site} 没有可用分配，无需隔离（已回收或不存在）"]

        assignment["status"] = ASSIGN_QUARANTINED
        assignment["pending"] = True
        assignment["abnormal"] = True
        assignment["隔离原因"] = f"连续{count}次识别失败（{captcha_type}/{provider}）"
        assignment["隔离时间"] = _fmt(now)
        assignment["隔离到期时间"] = _fmt(now + timedelta(minutes=QUARANTINE_MINUTES))
        assignment["解除原因"] = ""
        result["关联分配"] = assignment.get("分配编号", "")

        notes = [f"分配 {assignment.get('分配编号')} 已隔离 {QUARANTINE_MINUTES} 分钟"]
        replacement = self._failover(assignment)
        if replacement is not None:
            notes.append(
                f"已按地域切换顺序启用 {replacement['分配编号']}"
                f"（{replacement['实例编号']}/{replacement['出站地域']}）接管 {site}"
            )
        else:
            notes.append(f"地域切换顺序内暂无可用代理接管 {site}，等待人工处理")
        return notes

    def _degrade_strategy(
        self,
        strategy: dict[str, Any],
        key: tuple[str, str, str, str],
        count: int,
        now: datetime,
    ) -> list[str]:
        if strategy.get("status") == STRATEGY_DEGRADED:
            return [f"策略 {strategy.get('策略编号')} 此前已降级，维持原结论"]
        _, _, proxy_code, site = key
        strategy["status"] = STRATEGY_DEGRADED
        strategy["pending"] = False
        strategy["abnormal"] = True
        strategy["降级原因"] = f"{proxy_code} 在 {site} 连续{count}次识别失败"
        strategy["降级时间"] = _fmt(now)
        return [f"策略 {strategy.get('策略编号')} 已降级"]

    def _route_result(self, strategy: dict[str, Any], result: dict[str, Any]) -> list[str]:
        if str(strategy.get("手动打码开关", "")).strip() == "开":
            result["status"] = RESULT_MANUAL
            result["pending"] = True
            result["处置说明"] = "转入人工打码队列"
            return ["识别结果转入人工打码队列，人工处理期间其他代理继续服务"]
        result["status"] = RESULT_DEGRADED
        result["处置说明"] = "策略已降级，结果不再自动识别"
        return ["识别结果随策略降级关闭，不再自动识别"]

    def _failover(self, quarantined: dict[str, Any]) -> dict[str, Any] | None:
        """按地域切换顺序，从当前地域的下一个开始为站点找接管的可用代理。"""
        site = str(quarantined.get("目标站点", ""))
        current = str(quarantined.get("出站地域", ""))
        order = REGION_FAILOVER_ORDER
        start = order.index(current) + 1 if current in order else 0
        regions = [region for region in order[start:] + order[:start] if region != current]

        assignments = store.rows(ASSIGN_MODULE)
        busy_pairs = {
            (str(row.get("实例编号", "")), str(row.get("目标站点", "")))
            for row in assignments
            if row.get("status") in (ASSIGN_SERVING, ASSIGN_QUARANTINED)
        }
        for region in regions:
            for proxy in store.rows(PROXY_MODULE):
                if proxy.get("status") != "可用" or str(proxy.get("出站地域", "")) != region:
                    continue
                proxy_code = str(proxy.get("实例编号", ""))
                if proxy_code == str(quarantined.get("实例编号", "")) or (proxy_code, site) in busy_pairs:
                    continue
                row: dict[str, Any] = {
                    "id": max((int(item.get("id", 0)) for item in assignments), default=0) + 1,
                    "分配编号": _next_code(assignments, "分配编号", "PASS-"),
                    "实例编号": proxy_code,
                    "目标站点": site,
                    "出站地域": region,
                    "分配来源": f"故障转移（接替 {quarantined.get('分配编号')}）",
                    "存活检测时间": str(proxy.get("存活检测时间", "")),
                    "隔离原因": "",
                    "隔离时间": "",
                    "隔离到期时间": "",
                    "解除原因": "",
                    "status": ASSIGN_SERVING,
                    "pending": False,
                    "abnormal": False,
                }
                assignments.append(row)
                return row
        return None


quarantine_engine = QuarantineEngine()
