# 参与贡献

## 最快的一条路：挑一个用例

```bash
python3 conformance/validate.py --dir conformance/cases/   # 期望 13/13
```

任何一条不符合预期，都值得开一个 issue。

## 提 issue

- **协议歧义**：某个字段在不同品类（CNC / 钣金 / 注塑 / 包装 / 物流 / 设计）里没法统一 —— 最有价值的一类 issue
- **校验器误判**：合法的报价被判不合规，或反过来
- **接入踩坑**：你的 AI 在按本协议产出报价时，卡在哪一步

## 提 PR

1. 改协议字段 = **破坏性变更**。先开 issue 说清楚为什么要改，别直接改 schema。
2. 校验器只依赖 Python 标准库（这是我们刻意的设计，别加第三方依赖）。
3. 新增用例：`conformance/cases/` 下放一份 `pass_*.json` 或 `fail_*.json`，`fail_*` 的文件名要能看出失败原因。
4. 跑完 `python3 conformance/validate.py --dir conformance/cases/` 和
   `python3 demo/run_demo.py --mock` 再提交。

## 命名约定

- wire 值：`quotespec/<major>.<minor>`，**不带域名、不用 urn**
- schema `$id`：`https://quotespec.dev/schema/quotespec-v<version>.json`

## 许可

贡献即表示同意以 Apache-2.0 发布。
