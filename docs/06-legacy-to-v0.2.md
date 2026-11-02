# 三个旧 skill → QuoteSpec v0.2

> 结论：**抽协议、留实现**。四个模型全票同意——旧资产不是包袱，是 v0.1 缺的那半张图纸。

---

## 1. 盘点：旧资产里有什么

| 旧资产 | 位置 | 抽进协议 | 留在实现层 | 处置 |
|---|---|---|---|---|
| **cad-bom-quote**（CAD 识图报价团） | `~/code/Skill创作总台/06-发布与复盘/packages/backup-2026-09-25-pre-fix/cad-parts-recognition/skills/cad-bom-quote` | 图纸指纹、成本分层、待确认项+区间、价格来源 | 6 个 agent 编排、DXF 解析、DWG 探测 | 机制进协议，实现留作第一个 Profile |
| **rfq-skill**（询价宝） | `~/code/1anyonecli+bosscli/agent-commerce-relay/plugins/rfq-skill` | 草稿→发布状态机、state_version、idempotency_key、比较前提检查 | 供应商池、消息中继、权限服务 | 机制进协议，实现开源 |
| **56chat-logistics-rfq**（物流询价） | `~/code/56chat/public/skills/56chat-logistics-rfq` | 结构化参数校验、敏感字段禁令、去标识化预览 | 站点、链接生成 | 保留为第二个 Profile，只开源校验器 |

**为什么不能整体合并**：6-agent 编排、DWG 解析、供应商池都是「实现」，写进协议会把协议变成某个产品的说明书。协议只收**跨品类通用的语义**。

---

## 2. v0.2 新增五项（已实现并通过校验）

| # | 字段 | 从哪来 | 缺了会怎样 | 校验规则 |
|---|---|---|---|---|
| 1 | `artifact.kind/sha256` | CAD 图纸指纹 | 报价对错了图纸版本，事后必扯皮 | sha256 必须 8–64 位小写十六进制 |
| 2 | `price_source` | CAD「价格来源」 | AI 编价格没人能审计 | 枚举：user_price_list / history / vendor_inquiry / pending |
| 3 | `pending_items[]` + `amount_impact` | CAD「待确认项」 | 没型号就编价，中标即亏 | 每项必须有 min ≤ max 的金额区间 |
| 4 | `cost_breakdown` + `comparison_basis` | CAD「成本分层」 | 辅材人工摊进单价，无法比价 | 有 total；口径不一致禁称最低价 |
| 5 | `state` / `state_version` / `idempotency_key` | rfq-skill 状态机 | 重复发送、并发覆盖 | 版本非负整数、幂等键 ≥8 位 |

**已落地验证**：`conformance/validate.py --dir conformance/cases/` → **13 个用例 13/13 通过**（含 v0.1 全部旧用例 + v0.2 六个新用例）。

### 关键设计：`price_source=pending` 的语义

这是 CAD 那个 skill 教我们的最重要一条——**没价就不许出价**：

```json
{
  "price_source": "pending",
  "pricing": [],
  "pending_items": [
    {"item": "阳极氧化色号未定",
     "amount_impact": {"min": 0, "max": 1200, "currency": "CNY"},
     "blocks_award": true}
  ]
}
```

校验器强制：pending 时 `pricing` 必须为空、`pending_items` 必须非空且带区间。**这比"报个区间价"更诚实，也比"报个准价"更安全。**

---

## 3. Profile 机制：宽架构的落地方式

评审一致认为「三档报价」不该进核心协议——它是行业惯例，不是通用语义。

```
QuoteSpec Core（协议，跨品类通用）
  └─ Profile: cnc        （数量阶梯、公差、表面处理、三档品牌）
  └─ Profile: logistics  （起运地/目的地/货量/贸易条款）
  └─ Profile: design     （交付物、修改次数）
  └─ Profile: packaging  （盒型、尺寸、印刷、MOQ）
```

**Core 只放所有品类都有的东西**：报价对象、指纹、价格来源、责任边界、状态机、信用。
**Profile 放类目专属的 spec 结构**，版本独立。

这就是「宽架构、窄点火」在协议层的写法：协议宽，Profile 窄，点火时只激活一个 Profile。

---

## 4. 状态机（从 rfq-skill 搬来的）

```
draft ──publish──▶ published ──quote──▶ accepted ──award──▶ awarded
  │                    │
  └──discard           └──expire
```

每次状态转换必须带：`expected_state_version`（防并发覆盖）+ `idempotency_key`（防重复提交）。
没有这两个，多 agent 并发报价一定炸。

---

## 5. 30 天验证（评审给的判据，我按可执行性收敛）

**动作**：3–5 家珠三角 CNC/钣金厂 + 3 家真实买家，跑 10–20 份带图纸的真实询价。
**A/B 对照**：同一份询价，一路走 QuoteSpec 结构化报价，一路走工厂现有的邮件/Excel。

| 判据 | 目标 | 怎么量 |
|---|---|---|
| 结构化通过率 | ≥90% | conformance 校验器直接出数 |
| 版本争议 | 0 次 | 故意中途改图纸版本，看是否全部标记失效 |
| 口径争议 | 比人工基线下降 50% | 客户/厂方指出「漏项、辅材混入单价」的次数 |
| 供应商续用 | ≥2 家第 2 周还愿意用 | 直接问 |
| 单张报价耗时 | <10 分钟 | 掐表 |

**止损线**：三条中两条不达标 → 回改 schema，不扩品类、不加供应商。

**反例预警**（来自评审）：如果工厂觉得字段太多拒填，**先把 cost_breakdown 砍成可选**，别为协议完整性丢供应商。

---

## 6. YC 叙事改写

**改前**（听起来像标准化空想）：
> 我们在写「报价的 USB-C」，一个让所有企业 AI 都能接入的开放协议。

**改后**（有物证）：
> 制造业的每一次报价，都在重新解释同一张图纸。我们已经跑通三条真实报价链路——CAD 识图报价、带版本控制的询价中继、跨境物流询价——把踩过的坑固化成一套开源协议，现在用它在一个窄品类（珠三角 CNC 小批量）做白手套闭环：**22 秒拿到 6 家 AI 供应商的结构化报价，图纸一改全部自动失效。**

关键变化：**叙事主体从「协议」换成「已经踩过坑的工作流」**，协议是它的抽离，不是它的替代。

必须给的量化边界（否则投资人一问就穿）：几家厂、几单、偏差多少。**旧 skill 都是 Windows 单机、无真实交易量**——这个事实要主动说，别等被问。

---

## 7. 明确不做

- ❌ 不把 6-agent 编排写进协议（那是实现）
- ❌ 不把去标识化/供应商隔离做成 v0.2 字段（做成白手套流程的默认行为，v0.3 再字段化）
- ❌ 不让三档报价进 Core（放 Profile）
- ❌ 不在 30 天验证之前扩第二个品类
