#!/usr/bin/env python3
"""离线 mock 报价：给 6 家示例供应商各返回一份符合协议的固定报价 payload。

用途：`python3 demo/run_demo.py --mock` 在**没有任何模型网关**的机器上，
也能跑完「发单 → 6 家报价 → 协议校验 → 比价 → 熔断 → 托管下单」的完整链路。

数字全部取自 run_demo.py 里各家的 pricelist；其中 sup_lianchuang 故意报低
（300 件 24.0、30000 件 11.0），低于同档中位数的 60%，用来演示「低价熔断」。
零依赖，只 import 标准库。
"""
from datetime import datetime, timedelta, timezone

# 报价有效期统一取「现在 + 7 天」，保证任何一天跑都不会过期。
WARRANTY = {"warranty_days": 180,
            "penalty": "延期交付按合同额 0.3%/天赔付，上限 10%",
            "rerun_policy": "非买方原因导致的返工由供应商承担全部费用"}


def _valid_until(days=7):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


def _pricing(*tiers):
    """(qty, unit, unit_price) → 协议 pricing 数组。"""
    return [{"qty": qty, "unit": unit, "unit_price": price,
             "currency": "CNY", "moq_ok": True} for qty, unit, price in tiers]


PAYLOADS = {
    # ---- CNC 模块 ----
    "sup_jingtuo": {
        "pricing": _pricing((300, "piece", 42.5), (30000, "piece", 18.9)),
        "lead_time_days": 15,
        "assumptions": ["按图纸公差 ±0.05mm 报价，关键尺寸 ±0.02mm 需二次确认",
                        "材料为 6061-T6 国产铝板，进口料另议",
                        "300 件一次性交付；30000 件按 3 批交付"],
        "exclusions": ["开模/工装费 8,000 元（一次性，未含在单价内）",
                       "运费不含（买家指定货代）",
                       "氧化黑之外的表面工艺另计"],
        "responsibility": dict(WARRANTY),
    },
    "sup_ruijie": {
        "pricing": _pricing((300, "piece", 45.0), (30000, "piece", 19.8)),
        "lead_time_days": 12,
        "assumptions": ["含阳极氧化黑色处理", "材料为 6061-T6 国产铝板",
                        "300 件一次性交付；30000 件按 2 批交付"],
        "exclusions": ["运费不含（买家指定货代）", "模具/刀版费另计",
                       "超出氧化黑的表面工艺另议"],
        "responsibility": dict(WARRANTY, warranty_days=12 * 15),
    },
    # 这一家故意报低价：24.0 与 11.0 都低于同档中位数 60% → 触发 2 条熔断
    "sup_lianchuang": {
        "pricing": _pricing((300, "piece", 24.0), (30000, "piece", 11.0)),
        "lead_time_days": 22,
        "assumptions": ["仅做 CNC 铣削，阳极氧化不含", "小作坊产能有限，大单需分批发",
                        "按现有图纸报价，改图需重报"],
        "exclusions": ["阳极氧化另计", "运费不含", "模具费不含"],
        "responsibility": dict(WARRANTY, warranty_days=60),
    },
    # ---- 包装模块 ----
    "sup_box": {
        "pricing": _pricing((300, "set", 38.0), (1000, "set", 27.5)),
        "lead_time_days": 18,
        "assumptions": ["MOQ 1000 套；300 套按打样价", "材料 157g 铜版纸 + 2mm 灰板",
                        "CMYK 印刷 + 烫金"],
        "exclusions": ["模具/刀版费 1,200 元（一次性）", "运费不含", "超出 CMYK+烫金的工艺另计"],
        "responsibility": dict(WARRANTY),
    },
    # ---- 物流模块 ----
    "sup_sea": {
        "pricing": _pricing((18.5, "cbm", 2050.0)),
        "lead_time_days": 32,
        "assumptions": ["含报关、拖车、海运；不含美国端派送", "按 18.5 cbm 计费，体积以实际打尺为准",
                        "旺季舱位需提前 14 天订"],
        "exclusions": ["美国端派送不含", "目的港查验/滞箱费不含", "货运保险不含"],
        "responsibility": dict(WARRANTY, warranty_days=0),
    },
    # ---- 设计费模块 ----
    "sup_design": {
        "pricing": _pricing((1, "set", 18000.0)),
        "lead_time_days": 20,
        "assumptions": ["含 2 次修改，超出按 1,200 元/次", "交付 STEP + 2D 工程图",
                        "不含模具跟踪"],
        "exclusions": ["模具跟踪不含", "打样费不含", "手板费用不含"],
        "responsibility": dict(WARRANTY),
    },
}


def _fallback(profile):
    """未收录的供应商：直接把它的 pricelist 翻译成 payload，保证 mock 永不失败。"""
    pl = profile.get("pricelist") or {}
    unit = "piece"
    tiers = pl.get("tiers") or [{"qty": 1, "unit_price": 1.0}]
    return {
        "pricing": _pricing(*[(t["qty"], unit, t["unit_price"]) for t in tiers]),
        "lead_time_days": int(pl.get("lead_time_days") or 15),
        "assumptions": list(pl.get("notes") or ["按价目表报价"]),
        "exclusions": ["运费不含", "税费按国家规定另计"],
        "responsibility": dict(WARRANTY),
    }


def payload_for(profile, line=None):
    """取某家供应商的 mock 报价 payload（每次调用刷新 valid_until）。"""
    base = PAYLOADS.get(profile.get("party_id")) or _fallback(profile)
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
           for k, v in base.items()}
    out["valid_until"] = _valid_until()
    return out
