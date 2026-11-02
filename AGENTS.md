# AGENTS.md — 在本仓库里干活的 AI，先读这 8 行

## 这是什么

QuoteSpec 是一个 **B2B 制造业报价协议**：把「报价」从一个自由文本，变成一份可校验的 JSON 对象。
它由三部分组成——`schema/`（协议）、`conformance/`（校验器 + 用例）、`demo/`（端到端参考实现）。

## 三条不许破的规矩

1. **校验器只依赖 Python 标准库。** 加任何第三方依赖都是回归。
2. **改协议字段 = 破坏性变更。** 先开 issue，别直接改 `schema/`。
3. **示例里的公司名必须带「示例」二字**，且不得指代任何真实在营企业。

## 改完必须跑的两条命令

```bash
python3 conformance/validate.py --dir conformance/cases/   # 13/13
python3 demo/run_demo.py --mock                            # 6/6，离线
```

## wire 值与 `$id` 的分工

- wire 值：`quotespec/<major>.<minor>` —— **不带域名、不用 urn**，它是协议标识符，换域名不该影响它。
- schema `$id`：`https://quotespec.dev/schema/quotespec-v<version>.json` —— 它是资源定位符，必须带 host。

## 不要做的事

- ❌ 把真实客户、真实公司名、真实报价写进 `samples/`
- ❌ 在 `demo/` 里硬编码任何本机地址或私有网关（只读 `OPENAI_BASE_URL` / `OPENAI_API_KEY`）
- ❌ 提交 `out/`、`engine/__pycache__/`、`demo/out/` 里的生成物
