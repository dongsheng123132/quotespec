# QuoteSpec

[![conformance](https://github.com/dongsheng123132/quotespec/actions/workflows/conformance.yml/badge.svg)](https://github.com/dongsheng123132/quotespec/actions/workflows/conformance.yml)
[![pages](https://github.com/dongsheng123132/quotespec/actions/workflows/pages.yml/badge.svg)](https://dongsheng123132.github.io/quotespec/)

> **在线演示：[dongsheng123132.github.io/quotespec/](https://dongsheng123132.github.io/quotespec/)**

> **B2B 制造业报价的开源协议** —— 把「报价单」从一个自由文本，变成一份可校验的 JSON 对象。
> RFQ（询价）→ Quote（报价）→ 比价 → 下单，全链路用同一套字段说话。

```bash
git clone https://github.com/dongsheng123132/quotespec && cd quotespec
python3 conformance/validate.py --dir conformance/cases/   # 13/13
python3 demo/run_demo.py --mock                            # 6 家 AI 报价，离线 1 秒，不需要任何 key
```

---

## 为什么需要它

今天的报价长这样：

> 「王总你好，这个件 300 个的话单价大概 45 左右，量大好商量，交期 15 天，不含运费，开票要另外加钱。」

这段话里有 **6 个无法机器校验的坑**：单价口径不明、数量档位没锁、运费没算、税率没提、有效期没给、`好商量` 是什么鬼。

买家用 Excel 手工整理三家报价，平均 40 分钟；整理完还比错——因为三家根本不在一个口径上。

QuoteSpec 干的事：**给「报价」这个对象定一套字段规范**，让任何企业的 AI（豆包工作、WorkBuddy、Codex、自建 agent）都能读单、报价、被校验。

## 三条硬规则

1. **单价口径统一**：含税出厂价。模具费、运费、税费必须单列，不许打包进 `unit_price`。
2. **四项强制**：`assumptions`（假设）/ `exclusions`（不含项）/ `valid_until`（有效期）/ `responsibility`（责任边界），缺一项报价作废。
3. **AI 必须打标**：`ai.human_confirmed = false` 的大额报价，不进比价池。

## 仓库里有什么

```
schema/
  quotespec-v0.1.schema.json    协议初版：RFQ / Quote / LineItem / PriceTier
  quotespec-v0.2.schema.json    v0.2：+ 工件指纹 / 价格来源 / 待确认项 / 成本分层 / 状态机
conformance/
  validate.py                   零依赖一致性校验器（只用 Python 标准库）
  cases/                        13 个通过 / 失败用例
samples/
  rfq-cnc-20-parts.json         一份真实形状的 RFQ：20 件零件 + 包装 + 海运 + 设计费
  quote-example.json            对应的 Quote
  raw-*.txt                     脏样例：微信口述 / 正规报价单 / 极简报价（喂给 engine 用）
demo/
  server.py                     平台侧最小实现：RFQ / Quote / 比价 / 熔断 / 托管 API
  supplier_agent.py             供应商 AI：读单 → 调模型 → 产出协议报价 → 提交
  mock_quotes.py                离线固定报价（`--mock` 用，不调任何模型）
  run_demo.py                   编排：发单 → 6 家 AI 报价 → 校验 → 熔断 → 下单
engine/
  checkup.py                    报价体检引擎：脏文本 → 协议对象 → 规则体检 → 可转发报告
site/
  rfq.html                      询盘结构化工具（纯前端，本地解析，不生成价格）
  checkup.html                  「报价体检」产品页
prototype/
  index.html                    单文件可点原型（拆模块 → 询价 → 比价 → 托管）
```

## 跑 demo

```bash
# 离线，不需要任何 API key（约 1 秒）
python3 demo/run_demo.py --mock

# 接真模型：任何 OpenAI 兼容端点都行
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=sk-...
python3 demo/run_demo.py
```

`--mock` 会完整走一遍：6 家供应商并发报价 → 协议拦下不合格报价 → 比价 → **低价熔断**（低于中位数 60% 转人工）→ 托管下单。

## 跑报价体检

把供应商发来的原文（微信口述、Excel 粘贴、PDF 转文字）变成一份能转发的报告：

```bash
python3 engine/checkup.py samples/raw-wechat-quote.txt --extract --out out/checkup-demo
python3 engine/checkup.py samples/raw-*.txt --extract --compare --out out/checkup-compare
```

引擎的规则层只核对、**绝不编价**：抽不出来的字段写进 `pending_items`，报告里写「没给」，不替供应商补数字。
抽字段那一步需要模型：`export OPENAI_BASE_URL=https://api.openai.com/v1 OPENAI_API_KEY=sk-...`，
模型可换（`CHECKUP_MODEL`，默认 `gpt-4o-mini`）。已经是 Quote JSON 的报价不需要模型，直接体检。

## 谁在用它

> 试用中的工厂与采购方见 [USERS.md](USERS.md)（有授权才列名）。

## 参与

- 协议歧义 / 校验器误判 / 接入踩坑 → 开 issue，见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 在本仓库里干活的 AI → 先读 [AGENTS.md](AGENTS.md)
- 版本变更 → [CHANGELOG.md](CHANGELOG.md)

## 许可

Apache-2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
`samples/` 与 `demo/` 中的公司名均为虚构示例，不指代任何真实企业。
