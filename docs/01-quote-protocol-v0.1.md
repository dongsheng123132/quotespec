# QuoteSpec 协议 v0.1

> **一句话**：让任何一家企业的 AI，在 30 分钟内读懂询价、写出结构化报价、接单成交。
>
> 它是「报价的 USB-C」——不是又一个 API，而是**报价对象的标准**。

---

## 0. 为什么需要这个协议

B2B 采购的死结不是「没有平台」，是**报价不可比**：

- 每个卖家报的东西长得不一样：含不含税、含不含运、公差怎么算、交期从哪天起算、模具费算不算。
- 每次询价都要人肉往返：发图纸 → 问价 → 补参数 → 再问价。
- 报价没有责任边界：出了事，买家说「你当初报的是这个价」，卖家说「你没说清公差」。

2026 年，企业已经有 AI 了（豆包工作、WorkBuddy、Codex、Muse……）。但 AI 之间**没有共同语言**。

QuoteSpec 就是这门语言。

**它解决的三件事**：
1. 同一个 line item 的报价，字段一致 → **机器能自动比价**。
2. 报价自带责任边界 → **事后不扯皮**。
3. 任何企业 AI 接一次 → **所有类目都能接**。

---

## 1. 设计原则

| # | 原则 | 含义 |
|---|---|---|
| 1 | **结构化优先** | 自由文本不计分；不可解析的报价不进比价池。 |
| 2 | **一单多模块** | 一个询价单可拆多个 line item（零件/包装/物流/设计费），各自独立报价、独立成交。 |
| 3 | **责任前置** | 报价必须写：假设、不含项、有效期、赔付规则。 |
| 4 | **AI 可读可写** | 一份 JSON Schema + 一段 system prompt = 可接入。 |
| 5 | **可验证** | 报价 vs 实际结算的一致性，进信用账本。 |
| 6 | **渐进披露** | 图纸分三级：脱敏特征 → 完整文件（NDA 后）→ 生产文件（托管后）。 |
| 7 | **只增不删** | 字段只增不删；破坏性变更升 minor 版本。 |

---

## 2. 对象模型

```
Party（企业主体）
  ├─ Credit（信用账本）
  └─ RFQ（询价单）
       └─ LineItem（可独立报价的模块）
             └─ Quote（报价）
                   └─ Award（成交）→ Order（托管）→ Review（验收回填）
```

一条主线：
**买家发 RFQ → 平台按类目定向广播 → 供应商 AI 读 RFQ 写 Quote → 买家比价 → 成交进托管 → 验收回填信用。**

---

## 3. 字段规范

### 3.1 Party（企业主体）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `party_id` | string | ✅ | 平台唯一 ID |
| `legal_name` | string | ✅ | 营业执照名称 |
| `verified` | bool | ✅ | 是否通过主体核验 |
| `capabilities` | string[] | ✅ | 能力标签，如 `["cnc_milling","anodizing"]` |
| `regions` | string[] | — | 可服务区域 |
| `credit` | Credit | — | 信用账本（见 3.3） |

### 3.2 RFQ（询价单）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `protocol` | string | ✅ | 固定 `"quotespec/0.1"` |
| `type` | string | ✅ | 固定 `"rfq"` |
| `id` | string | ✅ | 平台生成 |
| `buyer` | PartyRef | ✅ | 买家主体 |
| `title` | string | ✅ | 人类可读标题 |
| `deadline` | datetime | ✅ | 报价截止 |
| `currency` | string | ✅ | ISO 4217，如 `CNY` |
| `lines` | LineItem[] | ✅ | 至少 1 个模块 |
| `constraints` | object | — | 贸易条款、税、付款方式 |

### 3.3 LineItem（模块）

**这是整个协议的核心。** 一个询价单里每个可独立报价的东西，都是一个 line item。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `line_id` | string | ✅ | 单内唯一，如 `L1` |
| `category` | string | ✅ | 类目，如 `cnc_part` / `packaging` / `logistics` / `design_fee` |
| `quantity_tiers` | QuantityTier[] | ✅ | 数量阶梯，支持「300 个 / 3 万个」分别报价 |
| `spec` | object | ✅ | 结构化规格（见下） |
| `required_by` | date | — | 交付要求日 |
| `acceptance` | object | — | 验收标准（AQL、首件、抽检） |
| `file_refs` | FileRef[] | — | 图纸/文件引用（分级披露） |

**常见类目的 `spec` 结构**：

```jsonc
// category: "cnc_part"
{
  "material": "6061-T6",
  "process": ["cnc_milling", "anodizing"],
  "tolerance": { "general": "±0.05mm", "critical": ["±0.02mm"] },
  "surface": "anodized_black",
  "drawing_std": "GB/T 1804-m"
}

// category: "packaging"
{
  "box_type": "rigid_magnet",
  "size_mm": [220, 160, 60],
  "print": "CMYK + 烫金",
  "material": "157g 铜版纸 + 灰板 2mm",
  "moq": 1000
}

// category: "logistics"
{
  "from": "Shenzhen, CN",
  "to": "Los Angeles, US",
  "mode": "sea_freight",
  "volume_cbm": 18.5,
  "weight_kg": 3200,
  "incoterms": "DDP"
}

// category: "design_fee"
{
  "scope": "结构设计 + 3D 建模",
  "deliverables": ["STEP", "2D 工程图"],
  "revisions": 2
}
```

### 3.4 Quote（报价）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `protocol` / `type` / `id` | string | ✅ | `"quotespec/0.1"` / `"quote"` / 平台生成 |
| `rfq_id` / `line_id` | string | ✅ | 指向被报价的模块 |
| `supplier` | PartyRef | ✅ | 报价方 |
| `pricing` | PriceTier[] | ✅ | 按数量阶梯报价 |
| `lead_time_days` | int | ✅ | 交期（自然日，从下单日算） |
| `valid_until` | datetime | ✅ | **有效期，过期作废** |
| `assumptions` | string[] | ✅ | 报价成立的前提（如「按图纸公差 ±0.05」） |
| `exclusions` | string[] | ✅ | **不含项**（如「模具费另计 8000 元」「运费不含」） |
| `responsibility` | object | ✅ | 质保、延期赔付、返工政策 |
| `ai` | object | ✅ | 是否 AI 生成、置信度、人工是否确认 |
| `attachments` | FileRef[] | — | 补充文件 |

**PriceTier**：

```json
{ "qty": 300, "unit": "piece", "unit_price": 42.5, "currency": "CNY", "moq_ok": true }
```

> ⚠️ 统一口径：单价为**含税出厂价**（`ex_works_incl_tax`），运费、模具费必须单列进 `exclusions` 或在 `pricing` 里以独立行给出，不允许「打包一个总价」。

### 3.5 Credit（信用账本）

| 字段 | 类型 | 说明 |
|---|---|---|
| `rfq_responded_30d` | int | 近 30 天响应询价数 |
| `median_response_minutes` | number | 中位响应时长 |
| `quotes_submitted_90d` | int | 近 90 天提交报价数 |
| `award_rate` | number | 采纳率（**锚定托管成交**，不是被点开） |
| `price_vs_final_delta` | number | 报价 vs 最终结算偏差（杀中途加价） |
| `on_time_delivery` | number | 准时交付率 |
| `first_pass_inspection` | number | 一次验收通过率 |
| `dispute_rate` | number | 争议率 |
| `sample_size_ok` | bool | 样本是否足够（不够不排名） |

---

## 4. 注入机制（机器可读规则）

> 这一节是评审共识里最硬的部分：广播不设门槛 = 下水道。以下规则应作为平台侧的**可配置策略**，不是文档。

### 4.1 准入（Admission）

| 规则 | 阈值（建议） |
|---|---|
| 主体核验 | 营业执照 + 法人/对公账户 |
| 能力验证 | 试报 1 个标准件，人工复核 |
| 保证金 | 按类目 0.5–2 万，可退 |
| 新户报价额度 | 每日 5 条，凭成交解锁 |

### 4.2 广播（Routing）

- 按 `category` + `capabilities` + `quantity_tiers` + `regions` 匹配，**不做全网群发**。
- 单条 RFQ 默认最多广播给 20 家；同主体 7 天内重复 RFQ 降权。
- 买家可指定「仅广播给已合作过的主体」。

### 4.3 反垃圾（Anomaly）

| 规则 | 动作 |
|---|---|
| 结构化字段缺失（交期/有效期/不含项） | 直接丢弃，不进比价池 |
| 报价低于同 line 中位数 40% | 熔断 → 人工复核 |
| 30 天内改价 > 3 次 | 降权 |
| 报价与结算偏差 > 10% | 扣信用分 |
| 关联账户（自买自卖） | 剔除成交、冻结 |

### 4.4 跳单防御

不指望「封联系方式」，靠**让留在平台更划算**：

- 托管资金 + 验收质保，
- 对账、发票、售后、融资，
- 佣金随复购递减（第 2 单 2%、第 5 单 1%）。

### 4.5 图纸分级披露

| 级别 | 内容 | 触发 |
|---|---|---|
| L0 脱敏特征 | 几何特征参数、包围盒、材料、公差区间（无完整图纸） | 广播即给 |
| L1 完整文件 | STEP/DWG + 基准 | 签电子 NDA 后 |
| L2 生产文件 | 完整工程图 + 工艺要求 | 托管付款后 |

所有下载带水印 + 审计日志。

---

## 5. 接入三步（给企业 AI）

```http
# 1) 拉你类目的询价
GET /v1/rfqs?category=cnc_part&status=open&limit=20
Authorization: Bearer <your-agent-token>

# 2) 写报价（结构化，schema 校验）
POST /v1/quotes
{ "protocol": "quotespec/0.1", "type": "quote", ... }

# 3) 接 webhook
POST <your-endpoint>  { "event": "quote.accepted", "quote_id": "..." }
```

> 详细 prompt 模板与校验规则见 `docs/02-ai-onboarding.md`。

---

## 6. 平台侧要维护的东西（成本很低）

1. **Schema 版本**：只增不删。
2. **核价基线**：按类目维护「材料费 + 工时 + 开模费」的基准价，用于偏离熔断。
3. **信用账本**：成交回填驱动。
4. **类目 spec 库**：每个类目一套 `spec` 结构（CNC/钣金/注塑/包装/物流/设计）。

> 平台不替供应商报价——**平台只做基线和校验**。这是「不背锅」和「有护城河」之间的那条线。

---

## 7. 版本与兼容

- 协议版本写在每个对象里：`"protocol": "quotespec/0.1"`。
- 新增字段：直接加，旧客户端忽略。
- 删除/改名：升 minor（`0.2`），双写至少 6 个月。
- 类目 spec 独立版本化，避免「加一个类目就要升协议」。

---

## 8. 开放问题（v0.2 要解决）

1. **多模块合并报价**：同一个供应商同时报 L1+L2+L3，是否给折扣？需要 `bundle` 字段。
2. **跨境结算**：币种、汇率锁定、税务（VAT/GST）的标准化。
3. **AI 报价的责任归属**：AI 报错价，责任在供应商还是平台？建议：供应商主体承担，平台只提供基线与熔断。
4. **图纸特征提取的可移植性**：STEP 之外，2D PDF 图纸怎么结构化。
5. **信用跨平台迁移**：企业能不能带着信用去别的平台？（这是长期标准化的关键）

---

## 9. 为什么这是资产

- 企业 AI 接一次 = 所有类目都能接 → **切换成本高**。
- 平台掌握 schema = 掌握协议 → **谁定标准谁收租**。
- 报价数据 + 结算数据 = **核价基线**，越用越准 → 这就是飞轮的燃料。

> 一句话收尾：**包罗万象是结果，不是起点；协议先行，类目后扩。**
