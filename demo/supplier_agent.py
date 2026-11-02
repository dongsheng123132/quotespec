#!/usr/bin/env python3
"""供应商侧报价 AI：读协议 RFQ → 调任意 OpenAI 兼容接口 → 产出 Quote JSON → 提交平台。

演示的目的：证明「企业 AI 30 分钟接入」不是口号——
只需要一段 system prompt + 一个价目表 + 两个 HTTP 调用。

两种运行模式（由 run_demo.py 决定）：
  1. 真模型：读 OPENAI_BASE_URL / OPENAI_API_KEY，POST {base}/chat/completions；
  2. 离线 mock：`python3 demo/run_demo.py --mock`，用 demo/mock_quotes.py 里的固定报价，
     不需要任何网关/密钥/网络，照样走完「校验 → 提交 → 比价 → 熔断」。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import mock_quotes  # noqa: E402  （同目录、零依赖）

# 任意 OpenAI 兼容端点都可以。**故意不给默认值**——没配就明确报错，
# 而不是偷偷打到一个本机端口上、再产出一份「6 家全部失败」的假报告。
#   export OPENAI_BASE_URL=https://api.openai.com/v1
#   export OPENAI_API_KEY=sk-...
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
GW = (BASE_URL + "/chat/completions") if BASE_URL else ""
GW_KEY = os.environ.get("OPENAI_API_KEY", "")

SYSTEM_TMPL = """你是【{name}】的报价 AI。你只处理工艺类目：{categories}。

我方价目表（唯一价格依据，不得编造）：
{pricelist}

硬规则：
1. 只对 category 在 {categories} 内的 line_item 报价。
2. unit_price 必须来自上面的价目表，按 RFQ 的 quantity_tiers 一一对应输出。
3. assumptions / exclusions / valid_until / responsibility / lead_time_days 必须填写。
4. 禁止把模具费、运费、税费打包进 unit_price，必须写进 exclusions。
5. valid_until 用 ISO8601 带时区，不超过 7 天。
6. 数量可能是小数（例如 18.5 cbm），unit_price 按该单位计价，不要四舍五入。
7. 输出：只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
"""

QUOTE_SHAPE = """输出 JSON 结构（严格按此，不要增删顶层字段）：
{
  "pricing": [{"qty": <int>, "unit": "piece", "unit_price": <number>, "currency": "CNY", "moq_ok": true}],
  "lead_time_days": <int>,
  "valid_until": "<ISO8601 带时区>",
  "assumptions": ["...", "..."],
  "exclusions": ["...", "..."],
  "responsibility": {"warranty_days": <int>, "penalty": "...", "rerun_policy": "..."}
}"""


def mock_mode():
    """是否离线 mock 模式：`--mock` 会设 DEMO_MOCK=1，也可自行 export DEMO_MOCK=1。"""
    return os.environ.get("DEMO_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def resolve_model(profile):
    """真实调用时用哪个模型 ID。

    优先级：DEMO_MODEL_<PARTY_ID> > DEMO_MODEL > profile["model"]（标准模型 ID）。
    """
    pid = profile.get("party_id", "")
    for key in ("DEMO_MODEL_" + pid.upper(), "DEMO_MODEL_" + pid, "DEMO_MODEL"):
        val = os.environ.get(key)
        if val:
            return val.strip()
    return profile.get("model") or "gpt-4o-mini"


def extract_json(text):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:
        return None


def call_model(model, system, user, timeout=240):
    if not GW:
        return None, "未配置 OPENAI_BASE_URL（离线跑请加 --mock）", 0.0, None
    if not GW_KEY:
        return None, "未配置 OPENAI_API_KEY（离线跑请加 --mock）", 0.0, None
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 1800,
        "temperature": 0.3,
        "stream": False,
    }).encode()
    req = urllib.request.Request(GW, data=body, headers={
        "Authorization": "Bearer " + GW_KEY,
        "Content-Type": "application/json",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", time.time() - t0, None
    # 只依赖 OpenAI 兼容响应里的标准字段；没有就不猜，写 "?"。
    served = (d.get("model") if isinstance(d, dict) else None) or "?"
    if isinstance(d, dict) and "error" in d:
        return None, str(d["error"])[:200], time.time() - t0, served
    text = (d["choices"][0]["message"].get("content") or "").strip()
    return text, None, time.time() - t0, served


def build_quote(profile, rfq, line, payload, model):
    """把模型产出的内容包进协议信封（身份/协议字段由平台侧确定）。"""
    return {
        "protocol": "quotespec/0.1",
        "type": "quote",
        "rfq_id": rfq["id"],
        "line_id": line["line_id"],
        "supplier": {
            "party_id": profile["party_id"],
            "legal_name": profile["legal_name"],
            "verified": True,
            "capabilities": profile["capabilities"],
            "regions": [profile["region"]],
        },
        "pricing": payload.get("pricing", []),
        "lead_time_days": payload.get("lead_time_days", 0),
        "valid_until": payload.get("valid_until", ""),
        "assumptions": payload.get("assumptions", []),
        "exclusions": payload.get("exclusions", []),
        "responsibility": payload.get("responsibility", {}),
        "ai": {"generated": True, "confidence": 0.8,
               "human_confirmed": profile.get("human_confirmed", True),
               "model": model},
    }


def run_agent(profile, model, rfq, base_url, log=print):
    """一个供应商 AI 的完整一轮：拉单 → 报价 → 校验 → 提交。"""
    t_start = time.time()
    lines = [l for l in rfq["lines"] if l["category"] in profile["categories"]]
    if not lines:
        return {"supplier": profile["party_id"], "status": "skip", "reason": "无匹配类目"}
    line = lines[0]

    system = SYSTEM_TMPL.format(
        name=profile["legal_name"],
        categories=", ".join(profile["categories"]),
        pricelist=json.dumps(profile["pricelist"], ensure_ascii=False, indent=2),
    )
    user = ("这是平台推给你的 RFQ，请对 line_id=" + line["line_id"] + " 报价。\n\n"
            + json.dumps({"rfq": {k: v for k, v in rfq.items() if k != "lines"},
                          "line": line}, ensure_ascii=False, indent=2)
            + "\n\n" + QUOTE_SHAPE)

    attempts, last_err = [], None
    for attempt in range(2):
        if mock_mode():
            text, err, dt, served = (
                json.dumps(mock_quotes.payload_for(profile, line), ensure_ascii=False),
                None, 0.0, "mock")
        else:
            text, err, dt, served = call_model(model, system, user)
        attempts.append({"attempt": attempt + 1, "dt": round(dt, 1), "served": served, "err": err})
        if err:
            last_err = err
            continue
        payload = extract_json(text)
        if payload is None:
            last_err = "模型未输出可解析 JSON"
            user = user + "\n\n上一次输出不是合法 JSON。请只输出 JSON 对象，不要任何其它文字。"
            continue

        quote = build_quote(profile, rfq, line, payload, model)
        # 干跑校验（mock 也走同一条路，证明协议校验在离线时同样生效）
        errs = post_json(base_url + "/v1/validate", quote).get("errors", [])
        if errs:
            last_err = "; ".join(errs)
            user = user + "\n\n上一次报价被平台拒收，原因：" + last_err + "\n请修正后重新输出完整 JSON。"
            continue

        resp = post_json(base_url + "/v1/quotes", quote, expect=201)
        total = round(time.time() - t_start, 1)
        return {"supplier": profile["party_id"], "supplier_name": profile["legal_name"],
                "status": "quoted" if resp.get("ok") else "rejected",
                "quote_id": resp.get("id"), "model": model, "served": served,
                "latency_s": total, "attempts": attempts,
                "lead_time_days": quote["lead_time_days"],
                "pricing": quote["pricing"],
                "exclusions": quote["exclusions"],
                "assumptions": quote["assumptions"]}

    return {"supplier": profile["party_id"], "supplier_name": profile["legal_name"],
            "status": "failed", "reason": last_err, "model": model,
            "latency_s": round(time.time() - t_start, 1), "attempts": attempts}


def post_json(url, payload, expect=None):
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "errors": [f"HTTP {e.code}"]}


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())
