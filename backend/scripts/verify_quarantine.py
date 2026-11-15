"""失败驱动代理隔离的端到端核对脚本。

核对对象：验证码策略 CAPT-0002 / CAPT-0003、代理实例 PROX-0002。
覆盖规则：
  1. 连续失败按（验证码类型、识别服务商、代理实例、目标站点）汇总，达到阈值隔离分配；
  2. 重复失败上报只累计一次（上报单号幂等）；
  3. 触发隔离后策略降级、识别结果转人工打码队列，其他代理按地域切换顺序接管站点；
  4. 隔离解除必须有一次新的存活检测；
  5. 代理实例回收与策略降级保持一致，失败组合不留可用分配；
  6. 识别成功清零连续失败计数。

前置条件：后端刚启动（内存数据为种子状态，CAPT-0002 仍是「策略验证中」）。
用法：cd backend && .venv/bin/python scripts/verify_quarantine.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"

PASS_COUNT = 0
FAIL_COUNT = 0


def call(method: str, path: str, values: dict | None = None) -> dict:
    body = json.dumps({"values": values} if values is not None else None).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=body if values is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request) as response:  # noqa: S310 - 本地联调地址
        return json.loads(response.read().decode("utf-8"))


def check(label: str, condition: bool, evidence: str) -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {label} —— {evidence}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label} —— {evidence}")


def find(rows: list[dict], field: str, value: str) -> dict | None:
    for row in rows:
        if str(row.get(field, "")) == value:
            return row
    return None


def list_all(module: str) -> list[dict]:
    return call("GET", f"/api/{module}?size=200")["items"]


def main() -> int:
    print("== 0. 前置状态核对（CAPT-0002 / CAPT-0003 / PROX-0002） ==")
    strategies = list_all("captcha")
    capt2 = find(strategies, "策略编号", "CAPT-0002")
    capt3 = find(strategies, "策略编号", "CAPT-0003")
    assigns = list_all("proxyassign")
    pass2 = find(assigns, "分配编号", "PASS-0002")
    results = list_all("captcharesult")
    if capt2 is None or capt2["status"] != "策略验证中":
        print("  当前不是种子状态（CAPT-0002 已变更）。请重启后端后再运行本脚本。")
        return 2
    check("初始状态", capt3["status"] == "已启用" and pass2["status"] == "可用",
          f"CAPT-0002={capt2['status']}，CAPT-0003={capt3['status']}，PASS-0002={pass2['status']}")
    check("历史失败已累计", find(results, "结果编号", "CAPR-0003")["连续失败数"] == 2,
          "CAPR-0003 连续失败数=2（滑块验证码/极验/PROX-0002/shop-alpha.example.com）")

    print("== 1. 重复失败上报只累计一次 ==")
    reply = call("POST", "/api/captcharesult", {
        "策略编号": "CAPT-0002", "验证码类型": "滑块验证码", "识别服务商": "极验",
        "实例编号": "PROX-0002", "目标站点": "shop-alpha.example.com",
        "识别结果": "识别失败", "失败原因": "滑块轨迹校验超时", "上报单号": "RPT-20261102-002",
    })
    results = list_all("captcharesult")
    check("重复上报幂等", reply["ok"] and len(results) == 4
          and find(results, "结果编号", "CAPR-0003")["连续失败数"] == 2,
          f"message={reply['message']}；识别结果总数={len(results)}")

    print("== 2. 第三次连续失败触发隔离 ==")
    reply = call("POST", "/api/captcharesult", {
        "策略编号": "CAPT-0002", "验证码类型": "滑块验证码", "识别服务商": "极验",
        "实例编号": "PROX-0002", "目标站点": "shop-alpha.example.com",
        "识别结果": "识别失败", "失败原因": "滑块轨迹校验超时", "上报单号": "RPT-20261115-001",
    })
    entry = reply["entry"]
    check("失败累计到达阈值", entry["结果编号"] == "CAPR-0005" and entry["连续失败数"] == 3,
          f"CAPR-0005 连续失败数={entry['连续失败数']}")
    check("识别结果转人工打码", entry["status"] == "人工打码中" and entry["关联分配"] == "PASS-0002",
          f"status={entry['status']}，处置说明={entry['处置说明']}")

    assigns = list_all("proxyassign")
    pass2 = find(assigns, "分配编号", "PASS-0002")
    check("代理分配已隔离", pass2["status"] == "隔离中" and bool(pass2["隔离到期时间"]),
          f"PASS-0002={pass2['status']}，隔离原因={pass2['隔离原因']}，到期={pass2['隔离到期时间']}")
    capt2 = find(list_all("captcha"), "策略编号", "CAPT-0002")
    check("策略已降级", capt2["status"] == "已降级" and capt2["abnormal"],
          f"CAPT-0002={capt2['status']}，降级原因={capt2.get('降级原因', '')}")
    failover = find(assigns, "分配编号", "PASS-0004")
    check("地域切换接管站点", failover is not None and failover["实例编号"] == "PROX-0003"
          and failover["出站地域"] == "华南" and failover["status"] == "可用",
          f"PASS-0004={failover['实例编号']}/{failover['出站地域']}/{failover['status']}（{failover['分配来源']}）")
    pass3 = find(assigns, "分配编号", "PASS-0003")
    check("人工处理期间其他代理继续服务", pass3["status"] == "可用",
          f"PASS-0003（PROX-0003/blog-beta.example.com）={pass3['status']}")

    print("== 3. 触发单重复上报不重复处置 ==")
    reply = call("POST", "/api/captcharesult", {
        "策略编号": "CAPT-0002", "验证码类型": "滑块验证码", "识别服务商": "极验",
        "实例编号": "PROX-0002", "目标站点": "shop-alpha.example.com",
        "识别结果": "识别失败", "失败原因": "滑块轨迹校验超时", "上报单号": "RPT-20261115-001",
    })
    check("触发单幂等", reply["ok"] and len(list_all("captcharesult")) == 5
          and len(list_all("proxyassign")) == 4,
          f"message={reply['message']}")

    print("== 4. CAPT-0003 不受影响 ==")
    capt3 = find(list_all("captcha"), "策略编号", "CAPT-0003")
    capr4 = find(list_all("captcharesult"), "结果编号", "CAPR-0004")
    check("健康策略保持启用", capt3["status"] == "已启用" and capr4["连续失败数"] == 1,
          f"CAPT-0003={capt3['status']}，CAPR-0004 连续失败数={capr4['连续失败数']}（未达阈值）")

    print("== 5. 隔离解除需要新的存活检测 ==")
    reply = call("POST", "/api/proxyassign/2/actions", {"action": "标记可用"})
    check("无新检测不得恢复", not reply["ok"], f"message={reply['message']}")
    reply = call("POST", "/api/proxyassign/2/actions", {"action": "提交存活检测"})
    pass2 = reply["entry"]
    check("重新提交存活检测", reply["ok"] and pass2["status"] == "待检测",
          f"PASS-0002={pass2['status']}，存活检测时间={pass2['存活检测时间']}")
    reply = call("POST", "/api/proxyassign/2/actions", {"action": "标记可用"})
    check("新检测通过后恢复", reply["ok"] and reply["entry"]["status"] == "可用",
          f"message={reply['message']}")

    print("== 6. 代理回收与策略降级一致 ==")
    reply = call("POST", "/api/proxypool/2/actions", {"action": "回收实例"})
    check("实例回收级联分配", reply["ok"] and "PASS-0002" in reply["message"], f"message={reply['message']}")
    assigns = list_all("proxyassign")
    pass2 = find(assigns, "分配编号", "PASS-0002")
    leftovers = [a["分配编号"] for a in assigns if a["实例编号"] == "PROX-0002" and a["status"] == "可用"]
    capt2 = find(list_all("captcha"), "策略编号", "CAPT-0002")
    check("失败组合不留可用分配", pass2["status"] == "已回收" and not leftovers,
          f"PASS-0002={pass2['status']}（{pass2['解除原因']}），残留可用分配={leftovers or '无'}")
    check("降级结论保持可解释", capt2["status"] == "已降级" and bool(capt2.get("降级原因")),
          f"CAPT-0002={capt2['status']}，降级原因={capt2.get('降级原因', '')}")

    print("== 7. 识别成功清零计数 ==")
    reply = call("POST", "/api/captcharesult", {
        "策略编号": "CAPT-0003", "验证码类型": "点选验证码", "识别服务商": "超级鹰",
        "实例编号": "PROX-0003", "目标站点": "blog-beta.example.com",
        "识别结果": "成功", "上报单号": "RPT-20261115-002",
    })
    check("成功清零连续失败", reply["ok"] and reply["entry"]["连续失败数"] == 0,
          f"CAPR-0006 连续失败数={reply['entry']['连续失败数']}，PASS-0003 保持可用")

    print(f"\n核对完成：{PASS_COUNT} 项通过，{FAIL_COUNT} 项失败。")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
