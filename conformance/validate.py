#!/usr/bin/env python3
"""QuoteSpec 一致性校验器（零依赖，支持 v0.1 / v0.2）。

用法：
    python3 validate.py <file.json>        # 按文件里的 protocol 自动选版本
    python3 validate.py --dir cases/       # 跑一个目录（pass_* 期望通过，fail_* 期望失败）
    python3 run.py                         # 跑全部 fixtures

退出码：0 = 全部符合预期；1 = 有不符合。
"""
import json
import os
import re
import sys

V10 = "quotespec/0.1"
V20 = "quotespec/0.2"
PROTOCOLS = {V10, V20}

QUOTE_REQUIRED = [
    "protocol", "type", "rfq_id", "line_id", "supplier", "pricing",
    "lead_time_days", "valid_until", "assumptions", "exclusions",
    "responsibility", "ai",
]
RFQ_REQUIRED = ["protocol", "type", "id", "buyer", "title", "deadline", "currency", "lines"]
LINE_REQUIRED = ["line_id", "category", "quantity_tiers", "spec"]

PRICE_SOURCES = {"user_price_list", "history", "vendor_inquiry", "pending"}
ARTIFACT_KINDS = {"drawing", "spec", "none"}
HEX8 = re.compile(r"^[a-f0-9]{8,64}$")


def _show(v):
    return v if isinstance(v, (str, int, float, bool)) or v is None else type(v).__name__


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _currency_ok(v):
    return isinstance(v, str) and len(v) == 3 and v.isupper()


def validate_quote(q, version="0.1"):
    errs = []
    if not isinstance(q, dict):
        return ["payload 必须是 JSON 对象"]
    for k in QUOTE_REQUIRED:
        if k not in q:
            errs.append(f"缺少字段 {k}")
    if q.get("protocol") not in PROTOCOLS:
        errs.append(f'protocol 必须为 "{V10}" 或 "{V20}"，实际 {_show(q.get("protocol"))}')
    if q.get("type") != "quote":
        errs.append('type 必须为 "quote"')
    if not isinstance(q.get("rfq_id"), str) or not q.get("rfq_id"):
        errs.append("rfq_id 必须是非空字符串")
    if not isinstance(q.get("line_id"), str) or not q.get("line_id"):
        errs.append("line_id 必须是非空字符串")

    sup = q.get("supplier")
    if not isinstance(sup, dict):
        errs.append("supplier 必须是对象")
    else:
        if not sup.get("party_id"):
            errs.append("supplier.party_id 必填")
        if not isinstance(sup.get("verified"), bool):
            errs.append("supplier.verified 必须是布尔值")

    # ---- pricing ----
    pricing = q.get("pricing")
    source = q.get("price_source")
    if version == "0.2" and source not in PRICE_SOURCES:
        errs.append("price_source 必须是 user_price_list / history / vendor_inquiry / pending")
    if not isinstance(pricing, list):
        errs.append("pricing 必须是数组")
    elif version == "0.2" and source == "pending":
        if pricing:
            errs.append("price_source=pending 时 pricing 必须为空数组")
        if not q.get("pending_items"):
            errs.append("price_source=pending 时 pending_items 必须非空")
    elif not pricing:
        errs.append("pricing 必须是非空数组")
    else:
        for i, p in enumerate(pricing):
            if not isinstance(p, dict):
                errs.append(f"pricing[{i}] 必须是对象")
                continue
            for k in ("qty", "unit", "unit_price", "currency", "moq_ok"):
                if k not in p:
                    errs.append(f"pricing[{i}] 缺少 {k}")
            up = p.get("unit_price")
            if up is None:
                errs.append(f"pricing[{i}].unit_price 为 null（缺价目依据不能报价）")
            elif not _is_num(up) or up <= 0:
                errs.append(f"pricing[{i}].unit_price 必须为正数")
            qty = p.get("qty")
            if not _is_num(qty) or qty <= 0:
                errs.append(f"pricing[{i}].qty 必须为正数")
            if not _currency_ok(p.get("currency")):
                errs.append(f"pricing[{i}].currency 必须是 3 位大写币种代码")

    lt = q.get("lead_time_days")
    if not isinstance(lt, int) or isinstance(lt, bool) or lt < 0:
        errs.append("lead_time_days 必须是非负整数")
    if not isinstance(q.get("valid_until"), str) or "T" not in str(q.get("valid_until")):
        errs.append("valid_until 必须是 ISO8601 时间字符串")

    if not isinstance(q.get("assumptions"), list) or not q.get("assumptions"):
        errs.append("assumptions 不能为空（必须写报价假设）")
    if not isinstance(q.get("exclusions"), list):
        errs.append("exclusions 必须是数组（没有不含项写空数组）")

    resp = q.get("responsibility")
    if not isinstance(resp, dict):
        errs.append("responsibility 必须是对象")
    elif "warranty_days" not in resp:
        errs.append("responsibility.warranty_days 必填")

    ai = q.get("ai")
    if not isinstance(ai, dict):
        errs.append("ai 必须是对象")
    else:
        for k in ("generated", "human_confirmed"):
            if not isinstance(ai.get(k), bool):
                errs.append(f"ai.{k} 必须是布尔值")
        c = ai.get("confidence")
        if c is not None and (not _is_num(c) or not 0 <= c <= 1):
            errs.append("ai.confidence 必须在 0–1 之间")

    # ---- v0.2 追加 ----
    if version == "0.2":
        art = q.get("artifact")
        if not isinstance(art, dict):
            errs.append("artifact 必须是对象（报价必须绑定工件版本）")
        else:
            if art.get("kind") not in ARTIFACT_KINDS:
                errs.append("artifact.kind 必须是 drawing / spec / none")
            if not isinstance(art.get("sha256"), str) or not HEX8.match(art.get("sha256", "")):
                errs.append("artifact.sha256 必须是 8–64 位小写十六进制")

        cb = q.get("comparison_basis")
        if not isinstance(cb, dict):
            errs.append("comparison_basis 必须是对象（口径不一致不许比价）")
        else:
            for k in ("tax_included", "freight_included"):
                if not isinstance(cb.get(k), bool):
                    errs.append(f"comparison_basis.{k} 必须是布尔值")
            if not _currency_ok(cb.get("currency")):
                errs.append("comparison_basis.currency 必须是 3 位大写币种")

        items = q.get("pending_items")
        if items is not None:
            if not isinstance(items, list):
                errs.append("pending_items 必须是数组")
            else:
                for i, it in enumerate(items):
                    if not isinstance(it, dict):
                        errs.append(f"pending_items[{i}] 必须是对象")
                        continue
                    if not it.get("item"):
                        errs.append(f"pending_items[{i}].item 必填")
                    if not isinstance(it.get("blocks_award"), bool):
                        errs.append(f"pending_items[{i}].blocks_award 必须是布尔值")
                    imp = it.get("amount_impact")
                    if not isinstance(imp, dict):
                        errs.append(f"pending_items[{i}].amount_impact 必填（待确认项必须给金额影响区间）")
                        continue
                    mn, mx = imp.get("min"), imp.get("max")
                    if not _is_num(mn) or not _is_num(mx) or mn < 0 or mx < mn:
                        errs.append(f"pending_items[{i}].amount_impact 需满足 0 ≤ min ≤ max")
                    if not _currency_ok(imp.get("currency")):
                        errs.append(f"pending_items[{i}].amount_impact.currency 必须是 3 位大写币种")

        bd = q.get("cost_breakdown")
        if bd is not None:
            if not isinstance(bd, dict) or not _is_num(bd.get("total")):
                errs.append("cost_breakdown 若提供，必须含数值型 total")
            else:
                for k in ("material", "consumables", "labor", "overhead", "margin"):
                    if k in bd and not _is_num(bd[k]):
                        errs.append(f"cost_breakdown.{k} 必须是数值")

        if "idempotency_key" in q:
            if not isinstance(q["idempotency_key"], str) or len(q["idempotency_key"]) < 8:
                errs.append("idempotency_key 至少 8 位")
        if "state_version" in q:
            if not isinstance(q["state_version"], int) or isinstance(q["state_version"], bool) or q["state_version"] < 0:
                errs.append("state_version 必须是非负整数")
        if "state" in q and q["state"] not in ("draft", "published", "accepted", "rejected", "awarded"):
            errs.append("state 非法")
    return errs


def validate_rfq(r, version="0.1"):
    errs = []
    if not isinstance(r, dict):
        return ["payload 必须是 JSON 对象"]
    for k in RFQ_REQUIRED:
        if k not in r:
            errs.append(f"缺少字段 {k}")
    if r.get("protocol") not in PROTOCOLS:
        errs.append(f'protocol 必须为 "{V10}" 或 "{V20}"')
    if r.get("type") != "rfq":
        errs.append('type 必须为 "rfq"')
    if not _currency_ok(r.get("currency")):
        errs.append("currency 必须是 3 位大写币种代码")
    lines = r.get("lines")
    if not isinstance(lines, list) or not lines:
        errs.append("lines 必须是非空数组")
    else:
        seen = set()
        for i, ln in enumerate(lines):
            if not isinstance(ln, dict):
                errs.append(f"lines[{i}] 必须是对象")
                continue
            for k in LINE_REQUIRED:
                if k not in ln:
                    errs.append(f"lines[{i}] 缺少 {k}")
            lid = ln.get("line_id")
            if lid in seen:
                errs.append(f"lines[{i}].line_id 重复：{lid}")
            seen.add(lid)
            tiers = ln.get("quantity_tiers")
            if not isinstance(tiers, list) or not tiers:
                errs.append(f"lines[{i}].quantity_tiers 必须是非空数组")
            if not isinstance(ln.get("spec"), dict):
                errs.append(f"lines[{i}].spec 必须是对象")
    return errs


def version_of(doc):
    if isinstance(doc, dict) and doc.get("protocol") == V20:
        return "0.2"
    return "0.1"


def detect(doc):
    if not isinstance(doc, dict):
        return None
    return doc.get("type")


def validate_doc(doc):
    kind = detect(doc)
    ver = version_of(doc)
    if kind == "quote":
        return validate_quote(doc, ver)
    if kind == "rfq":
        return validate_rfq(doc, ver)
    return [f'无法识别 type：{_show(kind)}（应为 "quote" 或 "rfq"）']


def validate_file(path):
    with open(path, encoding="utf-8") as fh:
        return validate_doc(json.load(fh))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "--dir":
        d = argv[2]
        files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
        bad = 0
        for fn in files:
            errs = validate_file(os.path.join(d, fn))
            expect_pass = fn.startswith("pass_")
            ok = (not errs) if expect_pass else bool(errs)
            print(f"{'✅' if ok else '❌'} {fn:38s} {'符合预期' if ok else '不符合预期'}")
            if not ok:
                bad += 1
                for e in errs[:5]:
                    print("     -", e)
        print(f"\n{len(files)} 个用例，{bad} 个不符合预期")
        return 0 if bad == 0 else 1

    errs = validate_file(argv[1])
    if errs:
        print(f"❌ {argv[1]} 不符合协议：")
        for e in errs:
            print("  -", e)
        return 1
    print(f"✅ {argv[1]} 符合 QuoteSpec")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
