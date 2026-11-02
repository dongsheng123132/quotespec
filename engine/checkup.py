#!/usr/bin/env python3
"""QuoteSpec 报价体检引擎（白手套版 MVP）

把供应商发来的一段报价原文（微信文字 / Excel 粘贴 / PDF 转文字），
变成一份可直接转发给老板或客户的「报价体检报告」。

用法：
    # 1) 已经结构化的 Quote JSON，直接体检（不调模型，零成本）
    python3 engine/checkup.py samples/quote-example.json

    # 2) 脏文本：先让模型抽成 QuoteSpec JSON，再体检
    python3 engine/checkup.py 微信报价.txt --extract

    # 3) 多家比价（每份一份原文，出对比表 + 低价熔断）
    python3 engine/checkup.py a.txt b.txt c.txt --extract --compare

    # 4) 出可转发的 HTML 报告
    python3 engine/checkup.py 微信报价.txt --extract --out out/checkup

环境变量（任意 OpenAI 兼容端点都能用）：
    CHECKUP_GW    默认为空；也可以直接给 OPENAI_BASE_URL，自动拼 /chat/completions
    CHECKUP_KEY   默认为空；也可复用 OPENAI_API_KEY
    CHECKUP_MODEL 默认 gpt-4o-mini（任何 OpenAI 兼容模型都行）

设计约束（别改）：
    1. 引擎只核对、绝不编价。任何字段抽不出来就写 null + 进 pending，不猜。
    2. 每条结论都必须能被读者追溯到原始报价里的一句话。
    3. 报告面向「不懂 JSON 的业务员」，第一屏只有一句人话结论。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "conformance"))
import validate as V  # noqa: E402  （零依赖校验器，13 用例全过）

# 任意 OpenAI 兼容端点都可以，**故意不给默认值**：没配就明确报错。
GW = os.environ.get("CHECKUP_GW", "") or (
    (os.environ.get("OPENAI_BASE_URL", "").rstrip("/") + "/chat/completions")
    if os.environ.get("OPENAI_BASE_URL") else "")
KEY = os.environ.get("CHECKUP_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("CHECKUP_MODEL", "gpt-4o-mini")
CNY = timezone(timedelta(hours=8))

LOWBALL = 0.6      # 低于同批中位数 60% 视为异常低价，转人工
NEAR_EXPIRY = 3    # 有效期少于 3 天视为临期


# ---------------------------------------------------------------- 抽字段
EXTRACT_SYSTEM = """你是一台「报价单抽取器」，不是报价 AI。你的唯一任务是把供应商发来的报价原文抽成结构化字段。

铁律：
1. 原文没写的，一律填 null 或空数组。绝对不许推算、不许估价、不许补全行业惯例。
2. 金额只抄原文数字，不换算汇率、不加税。
3. 原文含糊的（比如「价格好商量」「量大优惠」），写进 notes，不要编成数字。
4. 只输出一个 JSON 对象，不要解释、不要 markdown 代码块。

输出结构：
{
 "supplier_name": "供应商名字或null",
 "part": "零件/工件的说法或null",
 "qty": 数字或null,
 "unit": "件/套/kg/米 等，拿不准写 null",
 "unit_price": 数字或null,
 "currency": "CNY 或 USD，拿不准按 CNY",
 "tax_included": true/false/null,
 "lead_time_days": 整数或null,
 "valid_until": "ISO8601 或原文说法或null",
 "assumptions": ["报价成立的前提，原文原话"],
 "exclusions": ["明确写了不含的东西，如模具费、运费、税费、打样费"],
 "extras": [{"name": "模具费/运费/打样费", "amount": 数字或null, "note": "原话"}],
 "warranty_days": 整数或null,
 "payment_terms": "账期原话或null",
 "notes": "含糊、矛盾、看不清的地方，逐条写"
}"""


def call_model(system, user, timeout=120):
    if not GW:
        raise RuntimeError("未配置 CHECKUP_GW 或 OPENAI_BASE_URL（例如 "
                           "export OPENAI_BASE_URL=https://api.openai.com/v1）")
    if not KEY:
        raise RuntimeError("未配置 CHECKUP_KEY 或 OPENAI_API_KEY")
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 1600,
        # 关键：deepseek/glm/claude 不关思考链会把预算全烧在 reasoning 上，content 变空
        "reasoning_effort": os.environ.get("CHECKUP_EFFORT", "none"),
    }).encode()
    req = urllib.request.Request(
        GW, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"模型调用失败（{GW}，model={MODEL}）：{last}")


def extract_json(text):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*", "", text.strip())
    t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except Exception:  # noqa: BLE001
        return None


def extract_quote(raw, src_name=""):
    """脏文本 → 扁平抽取结果。抽不出来就抛错，不降级成瞎猜。"""
    out = extract_json(call_model(EXTRACT_SYSTEM, f"报价原文如下：\n\n{raw}"))
    if not isinstance(out, dict):
        raise RuntimeError(f"{src_name}: 模型没能抽出 JSON，原文可能不是一份报价")
    out["_source"] = src_name
    out["_raw"] = raw.strip()
    return out


# ---------------------------------------------------------------- 转协议对象
def _iso(dt):
    return dt.astimezone(CNY).isoformat(timespec="seconds")


def _parse_valid(v):
    if not v:
        return None
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(s[:10], fmt).replace(tzinfo=CNY)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CNY)
        except ValueError:
            return None
    return None


def to_quote(flat, rfq_id="RFQ-DEMO", line_id="L1"):
    """扁平抽取结果 → QuoteSpec v0.2 Quote 对象。

    规矩：抽不出来的字段一律「保守取值 + 记进 pending_items」，
    绝不假装知道。税费未知时按「未声明即不含」处理，这是比价时唯一安全的默认。
    """
    up = flat.get("unit_price")
    qty = flat.get("qty")
    src = "user_price_list" if up is not None else "pending"
    pricing = []
    if up is not None and qty is not None:
        pricing.append({"qty": qty, "unit": flat.get("unit") or "piece",
                        "unit_price": up, "currency": flat.get("currency") or "CNY",
                        "moq_ok": True})

    def pend(item, why, blocks=True):
        return {"item": item, "note": why, "blocks_award": blocks,
                "amount_impact": {"min": 0, "max": 0, "currency": flat.get("currency") or "CNY"}}

    pending = []
    if up is None:
        pending.append(pend("单价", "原文没给明确单价"))
    if qty is None:
        pending.append(pend("数量", "原文没写数量"))
    if flat.get("tax_included") is None:
        pending.append(pend("税费口径", "原文没说含税还是不含税，按「未声明即不含」保守处理", False))
    raw_valid = flat.get("valid_until")
    vu = _parse_valid(raw_valid)
    if vu is None:
        pending.append(pend("有效期", f"原文写的是「{raw_valid}」，没有确切日期"))

    # 原文指纹：报价绑定的那一版原件，改了就是另一份报价
    sha = hashlib.sha256((flat.get("_raw") or "").encode()).hexdigest()

    extras = [e for e in (flat.get("extras") or [])
              if isinstance(e, dict) and isinstance(e.get("amount"), (int, float))]
    cb = None
    if extras:
        cb = {"total": sum(e["amount"] for e in extras), "extras": extras}

    ti = flat.get("tax_included")
    fi = flat.get("freight_included")
    if fi is None:
        ex_text = " ".join(flat.get("exclusions") or [])
        fi = False if any(w in ex_text for w in ("运费", "物流", "快递", "货代", "出厂")) else None

    q = {
        "protocol": "quotespec/0.2",
        "type": "quote",
        "id": f"Q-{int(time.time())}",
        "rfq_id": rfq_id,
        "line_id": line_id,
        "supplier": {"party_id": (flat.get("supplier_name") or "未署名供应商")[:60],
                     "verified": False},
        "pricing": pricing,
        "lead_time_days": flat.get("lead_time_days") if isinstance(flat.get("lead_time_days"), int) else 0,
        "valid_until": _iso(vu or datetime.now(CNY)),
        "assumptions": [a for a in (flat.get("assumptions") or []) if isinstance(a, str)],
        "exclusions": [a for a in (flat.get("exclusions") or []) if isinstance(a, str)],
        "responsibility": {"warranty_days": flat.get("warranty_days") or 0},
        "ai": {"generated": True, "human_confirmed": False, "model": MODEL},
        "artifact": {"kind": "spec" if flat.get("part") else "none",
                     "sha256": sha,
                     "captured_at": _iso(datetime.now(CNY)),
                     "note": "指纹取自报价原文，原文一改即失效"},
        "price_source": src,
        "comparison_basis": {"tax_included": bool(ti),
                             "freight_included": bool(fi),
                             "currency": flat.get("currency") or "CNY"},
        "state": "draft",
        "state_version": 1,
        # 私有旁证（校验器会忽略下划线键）：给报告层区分「没给」和「给了但过了期」
        "_valid_until_raw": raw_valid,
        "_valid_until_known": vu is not None,
    }
    if pending:
        q["pending_items"] = pending
    if cb:
        q["cost_breakdown"] = cb
    return q


# ---------------------------------------------------------------- 体检规则
def finding(level, code, title, detail, ask=None):
    return {"level": level, "code": code, "title": title, "detail": detail, "ask": ask}


def check(quote, flat=None):
    """规则体检：协议合规 + 商务硬伤 + 临期/过期。返回 findings 列表。"""
    flat = flat or {}
    fs = []

    # 1. 协议层校验（复用 conformance/validate.py）
    errs = V.validate_doc(quote)
    if errs:
        for e in errs[:8]:
            fs.append(finding("red", "SPEC", "协议不合规", e))
    else:
        fs.append(finding("green", "SPEC", "协议合规", "13 项必填全部到位，可进比价池"))

    # 2. 四项强制里的「假设」
    if not quote.get("assumptions"):
        fs.append(finding("red", "ASSUME", "没写报价假设",
                          "报价在什么前提下成立完全空白（数量、材质、公差、表面处理都没锁）",
                          "这批件按什么材质/公差/表面处理报的？数量变化多少要重新报价？"))

    # 3. 不含项
    ex = quote.get("exclusions") or []
    ex_text = " ".join(ex)
    # 同义词组：别因为供应商写「开模/工装费」就误判成没提模具费
    ALIAS = {
        "模具": ("模具", "开模", "工装", "模架"),
        "运费": ("运费", "物流", "快递", "货代", "运输"),
        "税": ("税",),
        "打样": ("打样", "样品", "首件"),
    }
    missing = [k for k, ws in ALIAS.items() if not any(w in ex_text for w in ws)]
    if not ex:
        fs.append(finding("red", "EXCL", "没有「不含项」",
                          "模具费、运费、税费是否包含完全没交代，签了就是扯皮",
                          "模具费/开模费是否包含？运费到付还是含运？含税还是不含税？"))
    elif missing:
        fs.append(finding("yellow", "EXCL", f"有 {len(missing)} 项费用没表态",
                          f"已写不含项：{'、'.join(ex) or '无'}；没提到的：{'、'.join(missing)}",
                          f"{'、'.join(missing)} 这几项怎么算？"))

    # 4. 有效期：必须区分「原文没写清」和「写了但过了期」——这是两种完全不同的处理
    now = datetime.now(CNY)
    raw_valid = quote.get("_valid_until_raw")
    if not quote.get("_valid_until_known"):
        fs.append(finding("yellow", "VALID", "没说清有效期",
                          f"原文写的是「{raw_valid or '没提'}」，抽不出确切日期。"
                          "价格什么时候失效没锁住，等于供应商随时可以涨",
                          "这个价格保到哪天？能不能锁价 30 天？"))
    else:
        vu = _parse_valid(quote.get("valid_until"))
        days = (vu - now).days
        if days < 0:
            fs.append(finding("red", "VALID", f"报价已过期 {abs(days)} 天",
                              f"有效期 {vu.date()}，今天已过",
                              "价格还有效吗？能按原价重报一次吗？"))
        elif days <= NEAR_EXPIRY:
            fs.append(finding("yellow", "VALID", f"还有 {days} 天到期",
                              f"有效期 {vu.date()}", "能不能锁价到月底？"))

    # 5. 交期
    lt = quote.get("lead_time_days")
    if not lt:
        fs.append(finding("yellow", "LEAD", "没写交期",
                          "lead_time_days 抽不出来", "最快交期几天？能不能分批交？"))

    # 6. 税费 / 运费口径（原文没说的，按「未声明即不含」保守处理，但必须讲清楚）
    raw = (flat or {}).get("_raw", "")
    if (flat or {}).get("tax_included") is None:
        fs.append(finding("yellow", "TAX", "含税口径不明",
                          "原文没说含税还是不含税，报告按「不含税」保守对齐——"
                          "如果别人报含税，你会直接比错 13%",
                          "这个价含 13% 增值税吗？能开专票吗？"))
    for e in (flat or {}).get("extras") or []:
        if isinstance(e, dict) and "开票" in str(e.get("name", "")):
            fs.append(finding("red", "INVOICE", "开票要另外加钱",
                              f"原文：「{e.get('note') or e.get('name')}」——"
                              "意味着现在这个价是「不开票价」，开票另算",
                              "开 13% 专票加几个点？加完的含税单价是多少？"))

    # 7. 待确认项
    pend = quote.get("pending_items") or []
    if pend:
        fs.append(finding("yellow", "PENDING", f"{len(pend)} 项关键信息原文没给",
                          "；".join(p.get("item", "") + "（" + (p.get("note") or p.get("why") or "") + "）"
                                    for p in pend)))

    # 8. 模糊话术
    vague = [w for w in ("大概", "左右", "看情况", "好商量", "再说", "差不多", "面议") if w in raw]
    if vague:
        fs.append(finding("yellow", "VAGUE", "报价里有模糊话术",
                          f"出现：{'、'.join(vague)}——这种话在验收时最容易变成加价理由",
                          "把「大概/看情况」的部分写成明确的数字或区间，可以吗？"))

    # 9. 抽不出来 = 供应商没给
    if quote.get("price_source") == "pending":
        fs.append(finding("red", "NOPRICE", "全文没有明确单价",
                          "供应商只给了口径没有给数字，无法比价"))

    return fs


def compare(entries, disp=None):
    """多份报价横向比价 + 低价熔断。entries: [(name, quote, findings)]"""
    disp = disp or {}
    priced = []
    for name, q, _ in entries:
        name = disp.get(name, name)
        for p in (q.get("pricing") or []):
            if isinstance(p.get("unit_price"), (int, float)) and p["unit_price"] > 0:
                priced.append((name, p["unit_price"], p.get("currency") or "CNY"))
                break
    if len(priced) < 2:
        return None
    vals = sorted(v for _, v, _ in priced)
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    rows, alarms = [], []
    for name, v, cur in sorted(priced, key=lambda x: x[1]):
        dev = (v - mid) / mid * 100 if mid else 0
        flag = ""
        if v < mid * LOWBALL:
            flag = "⚠ 异常低价"
            alarms.append(f"{name} 报 {v:g}{cur}，只有同批中位数 {mid:g} 的 {v/mid*100:.0f}%——"
                          "低于 60% 的报价通常意味着漏工序、换料或后面加价，转人工核")
        elif v > mid * 1.6:
            flag = "偏高"
        rows.append({"name": name, "price": v, "currency": cur,
                     "dev": dev, "flag": flag})
    return {"median": mid, "rows": rows, "alarms": alarms}


# ---------------------------------------------------------------- 出报告
def verdict(findings):
    red = [f for f in findings if f["level"] == "red"]
    yel = [f for f in findings if f["level"] == "yellow"]
    if red:
        return "red", f"先别签：有 {len(red)} 处硬伤"
    if yel:
        return "yellow", f"可以谈：有 {len(yel)} 处必须先问清楚"
    return "green", "可以直接进比价池"


def top_asks(findings, n=3):
    asks = [f["ask"] for f in findings if f.get("ask")]
    seen, out = set(), []
    for a in asks:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:n]


def report_md(entries, cmp_res, meta):
    level, head = verdict(entries[0][2]) if len(entries) == 1 else ("yellow", "多家比价已完成，注意异常低价")
    icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}[level]
    L = []
    L.append(f"# 报价体检报告 · {meta['date']}\n")
    L.append(f"**{icon} {head}**\n")
    if len(entries) == 1:
        q = entries[0][1]
        flat = meta["flats"].get(entries[0][0], {})
        sup = (q.get("supplier") or {}).get("party_id") or ""
        if sup in ("", "未署名供应商"):
            sup = (meta.get("disp") or {}).get(entries[0][0]) or entries[0][0]
        L.append(f"- 供应商：**{sup}**")
        if flat.get("part"):
            L.append(f"- 工件：{flat['part']}")
        pr = (q.get("pricing") or [{}])[0]
        if pr.get("unit_price"):
            L.append(f"- 报价：**¥{pr['unit_price']:g} / {pr.get('unit', '件')}**"
                     f"（{'含税' if (q.get('comparison_basis') or {}).get('tax_included') else '含税未知'}）")
        if q.get("lead_time_days"):
            L.append(f"- 交期：{q['lead_time_days']} 天")
        L.append("")
        L.append("## 一句话\n")
        L.append(meta["oneline"])
        L.append("")
    L.append("## 体检表\n")
    L.append("| 项 | 结果 | 说明 |")
    L.append("|---|---|---|")
    mark = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    for f in entries[0][2]:
        L.append(f"| {f['title']} | {mark[f['level']]} | {f['detail']} |")
    L.append("")
    if cmp_res:
        L.append("## 比价（同一批件的报价中位数）\n")
        L.append(f"中位数：**{cmp_res['median']:g}**\n")
        L.append("| 供应商 | 单价 | 偏离中位 | 提示 |")
        L.append("|---|---|---|---|")
        for r in cmp_res["rows"]:
            L.append(f"| {r['name']} | {r['price']:g} {r['currency']} | "
                     f"{r['dev']:+.0f}% | {r['flag']} |")
        L.append("")
        if cmp_res["alarms"]:
            L.append("### ⚠ 低价熔断\n")
            for a in cmp_res["alarms"]:
                L.append(f"- {a}")
            L.append("")
    asks = top_asks(entries[0][2])
    if asks:
        L.append("## 签字前必须问供应商的几件事\n")
        for i, a in enumerate(asks, 1):
            L.append(f"{i}. {a}")
        L.append("")
    L.append("---\n")
    L.append("> 本报告只做「口径核对」，不生成价格、不替代采购决策。"
             "所有结论都能在原始报价里找到对应原话；原文没写的，报告只会写「没给」，不会替供应商补。")
    return "\n".join(L)


def report_html(md, meta, entries):
    """极简浅色报告页：单蓝强调、无外部资源、手机可读。"""
    import html as H

    level, head = verdict(entries[0][2])
    color = {"red": "#c0392b", "yellow": "#b9770e", "green": "#1e8449"}[level]
    rows = "".join(
        f"<tr><td>{H.escape(f['title'])}</td>"
        f"<td class='lv' style='color:{color if f['level']=='red' else ('#b9770e' if f['level']=='yellow' else '#1e8449')}'>"
        f"{'●' if f['level']=='red' else ('◐' if f['level']=='yellow' else '○')}</td>"
        f"<td>{H.escape(f['detail'])}</td></tr>"
        for f in entries[0][2])
    asks = "".join(f"<li>{H.escape(a)}</li>" for a in top_asks(entries[0][2]))
    cmp_html = ""
    if meta.get("cmp"):
        r = "".join(f"<tr><td>{H.escape(x['name'])}</td><td>{x['price']:g} {x['currency']}</td>"
                    f"<td>{x['dev']:+.0f}%</td><td>{H.escape(x['flag'])}</td></tr>"
                    for x in meta["cmp"]["rows"])
        cmp_html = (f"<h2>比价</h2><p>同批中位数 <b>{meta['cmp']['median']:g}</b></p>"
                    f"<table><tr><th>供应商</th><th>单价</th><th>偏离</th><th>提示</th></tr>{r}</table>")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>报价体检报告 · {meta['date']}</title>
<style>
:root{{--ink:#1a1a1a;--mute:#6b6b6b;--line:#e6e6e6;--blue:#185abd}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f7f8fa;color:var(--ink);
font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:24px 18px 60px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:22px 22px 18px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:26px 0 8px;color:var(--blue)}}
.meta{{color:var(--mute);font-size:13px}}
.verdict{{display:inline-block;margin:14px 0 6px;padding:6px 14px;border-radius:6px;
font-weight:600;color:#fff;background:{color}}}
table{{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mute);font-weight:600;font-size:13px}}
.lv{{width:24px;text-align:center}}
ol{{padding-left:22px}}li{{margin:6px 0}}
.foot{{color:var(--mute);font-size:12px;margin-top:22px;border-top:1px solid var(--line);padding-top:12px}}
</style></head><body><div class="wrap"><div class="card">
<h1>报价体检报告</h1>
<div class="meta">{meta['date']} · QuoteSpec Checkup</div>
<div class="verdict">{H.escape(head)}</div>
<p>{H.escape(meta['oneline'])}</p>
<h2>体检表</h2><table><tr><th>项</th><th></th><th>说明</th></tr>{rows}</table>
{cmp_html}
<h2>签字前必须问供应商的几件事</h2><ol>{asks}</ol>
<div class="foot">本报告只做口径核对，不生成价格、不替代采购决策。原文没写的，报告写「没给」，不替供应商补。</div>
</div></div></body></html>"""


# ---------------------------------------------------------------- 主流程
def load_one(path, do_extract):
    p = Path(path)
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not do_extract:
        doc = json.loads(raw)
        return p.stem, doc, None
    flat = extract_quote(raw, p.name)
    return p.stem, to_quote(flat), flat


def main(argv=None):
    ap = argparse.ArgumentParser(description="QuoteSpec 报价体检引擎")
    ap.add_argument("files", nargs="+", help="报价文件：Quote JSON 或原文（配合 --extract）")
    ap.add_argument("--extract", action="store_true", help="先把脏文本抽成 QuoteSpec 再体检")
    ap.add_argument("--compare", action="store_true", help="多家横向比价")
    ap.add_argument("--out", help="输出目录（生成 report.md / report.html / quote.json）")
    ap.add_argument("--json", action="store_true", help="只输出机器可读结果")
    a = ap.parse_args(argv)

    entries, flats = [], {}
    for f in a.files:
        try:
            name, q, flat = load_one(f, a.extract)
        except Exception as e:  # noqa: BLE001
            print(f"[跳过] {f}: {e}", file=sys.stderr)
            continue
        flats[name] = flat or {}
        entries.append((name, q, check(q, flat)))
    if not entries:
        print("没有可体检的报价。", file=sys.stderr)
        return 2

    disp = {n: (flats.get(n) or {}).get("supplier_name") or n for n, _, _ in entries}
    cmp_res = compare(entries, disp) if (a.compare and len(entries) > 1) else None
    level, head = verdict(entries[0][2])
    lv = {"red": "🔴", "yellow": "🟡", "green": "🟢"}[level]
    one = {
        "red": f"{head}，先别回复供应商。下面是必须在签字前问清的清单。",
        "yellow": f"{head}，这份报价能用，但差几项关键信息，先问再签。",
        "green": "口径齐全，可以直接进比价池。",
    }[level]
    meta = {"date": datetime.now(CNY).strftime("%Y-%m-%d %H:%M"), "oneline": one,
            "flats": flats, "cmp": cmp_res, "disp": disp}

    if a.json:
        print(json.dumps({"verdict": level, "headline": head,
                          "findings": entries[0][2], "compare": cmp_res,
                          "quotes": {n: q for n, q, _ in entries}},
                         ensure_ascii=False, indent=2))
        return 0

    md = report_md(entries, cmp_res, meta)
    print(f"\n{lv} {head}")
    for f in entries[0][2]:
        m = {"red": "  [红]", "yellow": "  [黄]", "green": "  [绿]"}[f["level"]]
        print(f"{m} {f['title']} —— {f['detail'][:60]}")
    asks = top_asks(entries[0][2])
    if asks:
        print("\n必须先问：")
        for i, x in enumerate(asks, 1):
            print(f"  {i}. {x}")
    if cmp_res and cmp_res["alarms"]:
        print("\n⚠ 低价熔断：")
        for x in cmp_res["alarms"]:
            print("  - " + x)

    if a.out:
        od = Path(a.out)
        od.mkdir(parents=True, exist_ok=True)
        (od / "report.md").write_text(md, encoding="utf-8")
        (od / "report.html").write_text(report_html(md, meta, entries), encoding="utf-8")
        for name, q, _ in entries:
            (od / f"{name}.quote.json").write_text(
                json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已生成：{od}/report.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
