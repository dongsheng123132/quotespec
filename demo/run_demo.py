#!/usr/bin/env python3
"""端到端演示：真 HTTP 服务 + 6 家供应商 AI，按协议报价、校验、比价、熔断、托管下单。

两种跑法（都不需要改代码）：
  python3 demo/run_demo.py --mock    # 离线：用 demo/mock_quotes.py 的固定报价，不调任何模型
  python3 demo/run_demo.py           # 真模型：读 OPENAI_BASE_URL / OPENAI_API_KEY
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as srv_mod
import supplier_agent as agent

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "http://127.0.0.1:8787"

# 公司名一律带「示例」二字：虚构，不指代任何真实在营企业。
# model 写标准模型 ID；单个供应商可被 DEMO_MODEL_<PARTY_ID> 覆盖。
SUPPLIERS = [
    # ---- CNC 模块：三家，其中一家故意低价（演示熔断）----
    {
        "party_id": "sup_jingtuo",
        "legal_name": "东莞市示例精工有限公司",
        "region": "CN-Dongguan",
        "capabilities": ["cnc_milling", "cnc_turning", "anodizing"],
        "categories": ["cnc_part"],
        "model": "gpt-4o-mini",
        "human_confirmed": True,
        "pricelist": {
            "material": "6061-T6", "process": ["cnc_milling", "anodizing"],
            "tiers": [{"qty": 300, "unit_price": 42.5}, {"qty": 30000, "unit_price": 18.9}],
            "lead_time_days": 15,
            "notes": ["开模/工装费 8000 元一次性，未含单价", "运费不含"],
        },
    },
    {
        "party_id": "sup_ruijie",
        "legal_name": "深圳市示例锐精密科技有限公司",
        "region": "CN-Shenzhen",
        "capabilities": ["cnc_milling", "anodizing"],
        "categories": ["cnc_part"],
        "model": "claude-3-5-haiku-20241022",
        "human_confirmed": True,
        "pricelist": {
            "material": "6061-T6", "process": ["cnc_milling", "anodizing"],
            "tiers": [{"qty": 300, "unit_price": 45.0}, {"qty": 30000, "unit_price": 19.8}],
            "lead_time_days": 12,
            "notes": ["含阳极氧化黑", "运费不含，模具费另计"],
        },
    },
    {
        "party_id": "sup_lianchuang",
        "legal_name": "惠州市示例联创五金厂",
        "region": "CN-Huizhou",
        "capabilities": ["cnc_milling"],
        "categories": ["cnc_part"],
        "model": "gemini-2.0-flash",
        "human_confirmed": True,
        "pricelist": {
            "material": "6061-T6", "process": ["cnc_milling"],
            "tiers": [{"qty": 300, "unit_price": 24.0}, {"qty": 30000, "unit_price": 11.0}],
            "lead_time_days": 22,
            "notes": ["不含阳极氧化（另计）", "运费不含", "小作坊产能有限，大单需分批发"],
        },
    },
    # ---- 包装模块 ----
    {
        "party_id": "sup_box",
        "legal_name": "深圳市示例盒印包装有限公司",
        "region": "CN-Shenzhen",
        "capabilities": ["packaging", "rigid_box", "hot_stamping"],
        "categories": ["packaging"],
        "model": "gpt-4o-mini",
        "human_confirmed": True,
        "pricelist": {
            "box_type": "rigid_magnet", "size_mm": [220, 160, 60],
            "print": "CMYK + 烫金", "material": "157g 铜版纸 + 灰板 2mm",
            "tiers": [{"qty": 300, "unit_price": 38.0}, {"qty": 1000, "unit_price": 27.5}],
            "lead_time_days": 18, "moq": 1000,
            "notes": ["MOQ 1000 套；300 套按打样价", "模具/刀版费 1200 元一次性"],
        },
    },
    # ---- 物流模块 ----
    {
        "party_id": "sup_sea",
        "legal_name": "深圳市示例海联国际货运代理有限公司",
        "region": "CN-Shenzhen",
        "capabilities": ["sea_freight", "customs", "ddp"],
        "categories": ["logistics"],
        "model": "gpt-4o-mini",
        "human_confirmed": True,
        "pricelist": {
            "from": "Shenzhen, CN", "to": "Los Angeles, US", "mode": "sea_freight",
            "tiers": [{"qty": 18.5, "unit_price": 2050.0}],
            "lead_time_days": 32,
            "notes": ["含报关、拖车、海运；不含美国端派送", "旺季舱位需提前 14 天订"],
        },
    },
    # ---- 设计费模块 ----
    {
        "party_id": "sup_design",
        "legal_name": "深圳市示例形造工业设计有限公司",
        "region": "CN-Shenzhen",
        "capabilities": ["industrial_design", "structural_design", "3d_modeling"],
        "categories": ["design_fee"],
        "model": "gemini-2.0-flash",
        "human_confirmed": True,
        "pricelist": {
            "scope": "结构设计 + 3D 建模", "deliverables": ["STEP", "2D 工程图"],
            "tiers": [{"qty": 1, "unit_price": 18000.0}],
            "lead_time_days": 20, "revisions": 2,
            "notes": ["含 2 次修改；超出按 1200 元/次", "不含模具跟踪"],
        },
    },
]


def wait_health(base, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if agent.get_json(base + "/health").get("ok"):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def line(char="─", n=78):
    print(char * n)


def main(argv=None):
    global BASE
    ap = argparse.ArgumentParser(
        description="QuoteSpec 端到端 demo：发单 → 6 家供应商报价 → 校验 → 比价 → 熔断 → 托管下单")
    ap.add_argument("--mock", action="store_true",
                    help="离线模式：用 demo/mock_quotes.py 的固定报价，不调任何模型（无需网关/密钥）")
    ap.add_argument("--port", type=int, default=8787,
                    help="本地平台的端口（默认 8787；被占用时换一个，如 --port 8791）")
    args = ap.parse_args(argv)

    if args.mock:
        os.environ["DEMO_MOCK"] = "1"
    mock = agent.mock_mode()
    BASE = f"http://127.0.0.1:{args.port}"

    # 1) 起平台
    srv = srv_mod.create_server(args.port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert wait_health(BASE), "server 没起来"
    print(f"✅ 平台已启动 {BASE}")
    if mock:
        print("🎬 模式：离线 mock（mock_quotes.py 固定报价，不调任何模型）")
    else:
        print(f"🎬 模式：真模型 · {agent.BASE_URL} · key={'(已设置)' if agent.GW_KEY else '(空)'}")

    # 2) 买家发 RFQ（用真实样例）
    rfq = json.load(open(os.path.join(ROOT, "samples", "rfq-cnc-20-parts.json"), encoding="utf-8"))
    rfq["id"] = "rfq_demo_" + str(int(time.time()))
    rfq["title"] = "20 件铝合金 CNC 结构件（含包装与海运）· demo"
    r = agent.post_json(BASE + "/v1/rfqs", rfq, expect=201)
    rfq_id = r["id"]
    print(f"✅ 买家发单 {rfq_id}，{len(rfq['lines'])} 个模块："
          + "、".join(l["category"] for l in rfq["lines"]))
    line()

    # 3) 6 家供应商 AI 并发报价
    where = "离线固定报价" if mock else f"真实调用模型（{agent.BASE_URL}）"
    print(f"⏳ {len(SUPPLIERS)} 家供应商 AI 正在读单报价（{where}）…")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(agent.run_agent, p, agent.resolve_model(p), rfq, BASE) for p in SUPPLIERS]
        for f in futs:
            results.append(f.result())
    print(f"✅ 报价阶段完成，总耗时 {time.time() - t0:.1f}s\n")

    for r in sorted(results, key=lambda x: x.get("supplier", "")):
        tag = {"quoted": "✅ 已报价", "failed": "❌ 失败", "rejected": "⛔ 被拒", "skip": "⏭ 跳过"}
        print(f"  {tag.get(r['status'], r['status'])}  {r.get('supplier_name', r['supplier'])}")
        print(f"      模型 {r.get('model')} → 实际服务 {r.get('served')}")
        if r.get("pricing"):
            for p in r["pricing"]:
                print(f"      {p['qty']:>8} {p.get('unit','')} @ {p['unit_price']} {p['currency']}"
                      f"  交期 {r['lead_time_days']} 天")
        if r.get("exclusions"):
            print(f"      不含：{'；'.join(r['exclusions'][:2])}")
        if r.get("reason"):
            print(f"      原因：{r['reason']}")
        print(f"      端到端 {r.get('latency_s')}s")
    line()

    # 4) 平台牙齿：故意提交一份不合格报价
    print("🦷 验证协议牙齿：提交一份缺 exclusions / unit_price=null 的报价")
    bad = {
        "protocol": "quotespec/0.1", "type": "quote", "rfq_id": rfq_id, "line_id": "L1",
        "supplier": {"party_id": "sup_cheater", "legal_name": "某低价钓鱼作坊", "verified": True},
        "pricing": [{"qty": 300, "unit": "piece", "unit_price": None, "currency": "CNY", "moq_ok": True}],
        "lead_time_days": 20, "valid_until": "2026-11-20T18:00:00+08:00",
        "assumptions": ["面议"], "responsibility": {"warranty_days": 30},
        "ai": {"generated": False, "human_confirmed": True},
    }
    resp = agent.post_json(BASE + "/v1/quotes", bad)
    print("  平台返回 422：" if not resp.get("ok") else "  平台异常放行：")
    for e in resp.get("errors", []):
        print("   -", e)
    line()

    # 5) 比价 + 熔断
    comp = agent.get_json(BASE + f"/v1/comparison?rfq_id={rfq_id}")
    print("📊 比价表")
    for l in comp["lines"]:
        print(f"  模块 {l['line_id']}（{l['category']}）：{l['quote_count']} 家报价")
        if not l["rows"]:
            print("     （暂无）")
            continue
        hdr = f"    {'供应商':<28}{'数量':>8}{'单价':>10}{'交期':>6}{'熔断':>6}"
        print(hdr)
        for row in l["rows"]:
            cb = "⚠️" if row["circuit_breaker"] else ""
            print(f"    {row['supplier_name'][:26]:<28}{row['qty']:>8}{row['unit_price']:>10}"
                  f"{row['lead_time_days']:>6}{cb:>6}")
        for f in l["circuit_breakers"]:
            print(f"    ⚠️ 熔断：{f['supplier']} 报 {f['unit_price']}，"
                  f"中位数 {f['median']} —— {f['reason']}")
    line()

    # 6) 托管下单（选合规且交期最短的一家）
    best = None
    for l in comp["lines"]:
        if l["category"] != "cnc_part":
            continue
        for row in l["rows"]:
            if row["circuit_breaker"] or row["qty"] != 300:
                continue
            if best is None or row["lead_time_days"] < best["lead_time_days"]:
                best = row
    order = None
    if best:
        order = agent.post_json(BASE + "/v1/orders", {
            "quote_id": best["quote_id"],
            "amount": best["unit_price"] * 300,
            "currency": "CNY",
        })
        print(f"🔒 托管下单：{best['supplier_name']} · 300 件 · "
              f"¥{best['unit_price'] * 300:,.2f} → 订单 {order['order']['id']}")
        print(f"   托管状态：{order['order']['escrow']['status']}")
    line()

    # 7) 落盘
    out = {"rfq_id": rfq_id, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode":
           "mock" if mock else "live",
           "agents": results, "comparison": comp, "order": order,
           "events": agent.get_json(BASE + "/v1/state")["events"]}
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    path = os.path.join(HERE, "out", "report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"📄 报告已保存：{path}")

    ok = sum(1 for r in results if r["status"] == "quoted")
    cbs = sum(len(l["circuit_breakers"]) for l in comp["lines"])
    print(f"\n结论：{ok}/{len(SUPPLIERS)} 家 AI 成功按协议报价；"
          f"协议拦下 1 份不合格报价；熔断 {cbs} 条低价异常。")
    if order:
        print(f"托管下单：{order['order']['id']}（{order['order']['escrow']['status']}）")
    srv.shutdown()

    if ok == 0:
        print("\n提示：本机没有模型网关时，加 --mock 可离线跑完整链路："
              "python3 demo/run_demo.py --mock")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
