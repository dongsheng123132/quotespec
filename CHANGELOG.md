# Changelog

本项目的版本号同时描述**协议版本**（wire 值 `quotespec/0.x`）与**仓库发布**（tag `v0.x.y`）。

## Unreleased

- 协议与仓库统一命名为 **QuoteSpec**（wire 值 `quotespec/0.1`、`quotespec/0.2`；此前草稿期用过别的名字，未对外发布，故不保留兼容别名）
- `demo/run_demo.py` 新增 `--mock`：不需要任何 API key，离线 1 秒跑通全流程
- `demo/` 改为读 `OPENAI_BASE_URL` / `OPENAI_API_KEY`，不再绑定任何本机网关
- `samples/` 与 `demo/` 中的公司名全部改为「示例」虚构名

## v0.2.0 — 2026-11-02

- `schema/quotespec-v0.2.json`：新增工件指纹（`artifact`）、价格来源（`price_source`）、
  待确认项（`pending_items`）、成本分层（`cost_breakdown`）、比较前提（`comparison_basis`）、状态机（`state`）
- `conformance/validate.py` 支持 v0.1 / v0.2 双版本，13 个通过/失败用例

## v0.1.0 — 2026-11-02

- `schema/quotespec-v0.1.json`：对象模型（RFQ / Quote / LineItem / PriceTier）
- 三条硬规则：单价口径统一、四项强制（假设 / 不含项 / 有效期 / 责任边界）、AI 必须打标
