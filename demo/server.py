#!/usr/bin/env python3
"""QuoteSpec 平台侧最小实现（纯标准库，无依赖）。

提供协议端点：
  POST /v1/rfqs            创建询价单
  GET  /v1/rfqs            拉取询价（?category=&status=open）
  POST /v1/quotes          提交报价（校验 + 熔断标记）
  POST /v1/validate        干跑校验，不落库
  GET  /v1/comparison      比价表（含偏离熔断）
  POST /v1/orders          托管下单
  GET  /v1/state           调试用全量状态
  GET  /health             健康检查

设计要点（对应协议文档第 4 节）：
  - 结构化字段缺失 → 直接拒绝，不进比价池
  - 报价低于同档中位数 60% → 标记 circuit_breaker，转人工复核
  - 采纳率 / 报价-结算偏差等信用字段由成交回填驱动

数据说明：demo/ 与 samples/ 中的公司名（均带「示例」二字）、报价、询盘均为虚构示例，
        不指代任何真实在营企业；如有雷同纯属巧合。
"""
import json
import statistics
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {"rfqs": {}, "quotes": {}, "orders": {}, "events": []}
LOCK = threading.Lock()

QUOTE_REQUIRED = [
    "protocol", "type", "rfq_id", "line_id", "supplier", "pricing",
    "lead_time_days", "valid_until", "assumptions", "exclusions",
    "responsibility", "ai",
]


def now_ms():
    return int(time.time() * 1000)


def validate_quote(q):
    """返回错误列表；空列表 = 通过。"""
    errs = []
    if not isinstance(q, dict):
        return ["payload 不是对象"]
    for k in QUOTE_REQUIRED:
        if k not in q:
            errs.append(f"缺少字段 {k}")
    if q.get("protocol") != "quotespec/0.1":
        errs.append('protocol 必须是 "quotespec/0.1"')
    if q.get("type") != "quote":
        errs.append('type 必须是 "quote"')
    if not q.get("assumptions"):
        errs.append("assumptions 不能为空（报价必须写假设）")
    if not isinstance(q.get("exclusions"), list):
        errs.append("exclusions 必须是数组（没有不含项就写空数组）")
    pricing = q.get("pricing")
    if not isinstance(pricing, list) or not pricing:
        errs.append("pricing 必须是非空数组")
    else:
        for i, p in enumerate(pricing):
            if not isinstance(p, dict):
                errs.append(f"pricing[{i}] 不是对象")
                continue
            for k in ["qty", "unit", "unit_price", "currency", "moq_ok"]:
                if k not in p:
                    errs.append(f"pricing[{i}] 缺少 {k}")
            if p.get("unit_price") is None:
                errs.append(f"pricing[{i}] unit_price 为 null（缺价目依据，不能报价）")
            elif not isinstance(p.get("unit_price"), (int, float)) or p["unit_price"] <= 0:
                errs.append(f"pricing[{i}] unit_price 必须为正数")
    resp = q.get("responsibility")
    if not isinstance(resp, dict) or "warranty_days" not in resp:
        errs.append("responsibility.warranty_days 必填")
    ai = q.get("ai")
    if not isinstance(ai, dict):
        errs.append("ai 必须是对象")
    else:
        for k in ["generated", "human_confirmed"]:
            if k not in ai:
                errs.append(f"ai.{k} 必填")
    if not isinstance(q.get("lead_time_days"), int) or q.get("lead_time_days") < 0:
        errs.append("lead_time_days 必须是非负整数")
    return errs


def _median_by_tier(rfq_id):
    """{(line_id, qty): median_unit_price}，只统计已接收报价。"""
    groups = {}
    for q in STATE["quotes"].values():
        if q["rfq_id"] != rfq_id or q.get("_status") != "accepted":
            continue
        for p in q.get("pricing", []):
            groups.setdefault((q["line_id"], p["qty"]), []).append(float(p["unit_price"]))
    out = {}
    for key, vals in groups.items():
        if len(vals) >= 2:
            out[key] = statistics.median(vals)
    return out


def build_comparison(rfq_id):
    rfq = STATE["rfqs"].get(rfq_id)
    if not rfq:
        return {"error": "rfq not found"}, 404
    medians = _median_by_tier(rfq_id)
    lines = []
    for line in rfq["lines"]:
        rows, flags = [], []
        for q in STATE["quotes"].values():
            if q["rfq_id"] != rfq_id or q["line_id"] != line["line_id"]:
                continue
            if q.get("_status") != "accepted":
                continue
            for p in q.get("pricing", []):
                key = (line["line_id"], p["qty"])
                med = medians.get(key)
                cb = bool(med and float(p["unit_price"]) < med * 0.6)
                if cb:
                    flags.append({
                        "supplier": q["supplier"].get("party_id"),
                        "qty": p["qty"],
                        "unit_price": p["unit_price"],
                        "median": round(med, 2),
                        "reason": "低于中位数 40%，熔断转人工复核",
                    })
                rows.append({
                    "supplier_id": q["supplier"].get("party_id"),
                    "supplier_name": q["supplier"].get("legal_name"),
                    "qty": p["qty"],
                    "unit_price": p["unit_price"],
                    "currency": p.get("currency"),
                    "lead_time_days": q.get("lead_time_days"),
                    "valid_until": q.get("valid_until"),
                    "ai_model": (q.get("ai") or {}).get("model"),
                    "human_confirmed": (q.get("ai") or {}).get("human_confirmed"),
                    "circuit_breaker": cb,
                    "exclusions": q.get("exclusions", []),
                    "quote_id": q.get("id"),
                })
        rows.sort(key=lambda r: (r["qty"], r["unit_price"]))
        lines.append({
            "line_id": line["line_id"],
            "category": line["category"],
            "quote_count": len({r["supplier_id"] for r in rows}),
            "rows": rows,
            "circuit_breakers": flags,
        })
    return {"rfq_id": rfq_id, "title": rfq["title"], "lines": lines,
            "medians": {f"{k[0]}@{k[1]}": round(v, 2) for k, v in medians.items()}}, 200


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, {"ok": True, "ts": now_ms()})
        if u.path == "/v1/rfqs":
            cat = (qs.get("category") or [None])[0]
            status = (qs.get("status") or ["open"])[0]
            out = []
            for rfq in STATE["rfqs"].values():
                if status and rfq.get("status") != status:
                    continue
                lines = [l for l in rfq["lines"] if not cat or l["category"] == cat]
                if not lines:
                    continue
                out.append({**rfq, "lines": lines})
            return self._send(200, {"rfqs": out})
        if u.path == "/v1/comparison":
            rfq_id = (qs.get("rfq_id") or [""])[0]
            payload, code = build_comparison(rfq_id)
            return self._send(code, payload)
        if u.path == "/v1/state":
            return self._send(200, {
                "rfqs": list(STATE["rfqs"].values()),
                "quotes": list(STATE["quotes"].values()),
                "orders": list(STATE["orders"].values()),
                "events": STATE["events"],
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._read()
        except Exception as e:
            return self._send(400, {"error": f"invalid json: {e}"})

        if u.path == "/v1/validate":
            body.setdefault("id", "q_dryrun")
            errs = validate_quote(body)
            return self._send(200, {"ok": not errs, "errors": errs})

        if u.path == "/v1/rfqs":
            rfq = dict(body)
            rfq.setdefault("id", "rfq_" + uuid.uuid4().hex[:10])
            rfq["status"] = "open"
            rfq["created_at"] = now_ms()
            with LOCK:
                STATE["rfqs"][rfq["id"]] = rfq
                STATE["events"].append({"t": now_ms(), "type": "rfq.created", "id": rfq["id"]})
            return self._send(201, {"ok": True, "id": rfq["id"]})

        if u.path == "/v1/quotes":
            body.setdefault("id", "q_" + uuid.uuid4().hex[:10])
            errs = validate_quote(body)
            if errs:
                with LOCK:
                    STATE["events"].append({"t": now_ms(), "type": "quote.rejected",
                                            "rfq_id": body.get("rfq_id"), "errors": errs})
                return self._send(422, {"ok": False, "errors": errs})
            q = dict(body)
            q.setdefault("id", "q_" + uuid.uuid4().hex[:10])
            q["_status"] = "accepted"
            q["_received_at"] = now_ms()
            with LOCK:
                STATE["quotes"][q["id"]] = q
                STATE["events"].append({"t": now_ms(), "type": "quote.submitted",
                                        "id": q["id"], "line_id": q["line_id"]})
            return self._send(201, {"ok": True, "id": q["id"]})

        if u.path == "/v1/orders":
            qid = body.get("quote_id")
            q = STATE["quotes"].get(qid)
            if not q:
                return self._send(404, {"error": "quote not found"})
            order = {
                "id": "o_" + uuid.uuid4().hex[:10],
                "quote_id": qid,
                "rfq_id": q["rfq_id"],
                "line_id": q["line_id"],
                "supplier_id": q["supplier"].get("party_id"),
                "escrow": {"amount": body.get("amount"), "currency": body.get("currency", "CNY"),
                           "status": "held"},
                "created_at": now_ms(),
            }
            with LOCK:
                STATE["orders"][order["id"]] = order
                STATE["events"].append({"t": now_ms(), "type": "order.escrowed", "id": order["id"]})
            return self._send(201, {"ok": True, "order": order})

        return self._send(404, {"error": "not found"})


def create_server(port=8787):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv


def serve_forever(port=8787):
    srv = create_server(port)
    print(f"QuoteSpec demo server on http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    serve_forever()
