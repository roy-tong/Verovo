# 买方可签核的月结报告（V-03）

> **状态：V-03 合成 fixture 验收。** 报告接在 V-02 canonical case 上，复用
> 既有 `canonical_finance` → `period_intake` → `task_economics` 链，不新建
> 第二套对账引擎。`revise` / `signoff` 见 V-04 的 period-revisions 文档；V-03 的 `review` 只记录
> 署名复核，不签核账期、不授权付款。

```bash
cd buyer-ledger
B="python3 -m buyer_ledger"

$B close init ./close-2026-10 --from-job ./bundle/period-job.json
$B close ingest ./close-2026-10 --bundle ./bundle
$B close mapping ./close-2026-10 --confirm --review-by finance \
    --control-domain usage=vendor     # 控制域未裁决时必填，见 close mapping 预览
$B close calculate ./close-2026-10          # 运行既有 canonical 重算链
$B close report ./close-2026-10             # 生成六份报告对象
$B close review ./close-2026-10 --by finance-reviewer \
    --scope "read report and reserve on credit" \
    --reservation "credit note not applied" \
    --next-review 2026-11-30
$B close status ./close-2026-10             # 只读 BuyerCloseCase 投影
```

## 1. 前置条件

`close calculate` 只接受已经人工确认字段映射、并生成 `canonical/` 的 V-02
案例。它不读原始账单重新做字段映射，也不绕过 `mapping-confirmed.json`。
`close report` 只接受当前的 `calculation.json`；canonical 快照变化后必须重新
calculate，否则报 `STALE_SOURCE`。`close review` 只接受已经生成的
`report/report.json`。

## 2. 六份报告对象

| 文件 | 用途 |
| --- | --- |
| `report.md` | 人读的月结报告：主文先给付款/续约建议、账单与未解释差额、任务验收、成本与价值判断；附录保留范围、五类金额、四轴与裁决、覆盖、逐行差异、处理清单、Task Economics、审核区、重放元数据 |
| `report.json` | `verovo.buyer-close-report/1`：机器可读的同一内容，含逐行 lineage、open items、review、limitations |
| `line-reconciliation.csv` | 每个账单行的互斥分类、共同支持金额、规则复算金额、差额、rule id/version、finding ids |
| `review-queue.csv` | 处理清单：谁补哪份证据、金额与 `unresolved/open` 状态；`confirmed_cause` 默认 null |
| `evidence-index.json` | 输入源、规则版本、逐行 `source_ref` / `usage_source_ref` / `rule_ref`、未审核 invoice 文档 |
| `manifest.json` | 输入 hash、规则版本、代码/算法版本、输出快照指纹、各报告文件 hash |

所有金额是 Decimal 字符串。报告只做汇总/投影；逐行复算仍由
`task_economics.recalculate_charge` 完成，跨源对账仍由 `period_intake` /
`canonical_finance` 完成。旧 SQLite 路径的 `reconcile.py` / `report.py` 不适用
于 canonical bundle，因此没有复制它们的算术，也没有另建引擎。

## 3. 五个核心金额与互斥分类

`report.json -> amounts`：

| 字段 | 口径 |
| --- | --- |
| `invoice_total` | 参与本报告的账单行合计；未应用 credit/refund 行不计入 |
| `rule_repriced_subtotal` | 有批准规则可复算的行的 expected amount 合计 |
| `matched_postings` / `distinct_source_reconciled` | 账单与独立用量行共同支持的金额（差额行取 min，不是整行金额） |
| `known_adjustments` | 已应用 tax/credit/discount 的合计 |
| `open_difference` | `invoice_total − matched_postings − known_adjustments` |

每一行只落入一个 bucket：

- `matched_posting`：账单与独立来源金额一致，整行进入共同支持金额；
- `explained_adjustment`：已应用的 tax/credit/discount，整行进入 known adjustments；
- `excluded_pending_adjustment`：issued/unapplied credit/refund，排除在 invoice
  total 之外，进入 open exposure，不是 savings；
- `residual`：未对账、部分对账、未知或未支持的调整，剩余金额进入 open difference。

因此 `invoice_total = matched_postings + known_adjustments + open_difference`
是按行恒等式，不是重复来源相加。一个 charge 被多个 findings 引用时，
`line_reconciliation` 仍然只有一行；`findings_policy.findings_never_summed`
明确禁止把 findings 金额再加一遍。

缺失 invoice/bill 行时，`invoice_total`、`matched_postings` 和
`open_difference` 都是 `null`，不是 `0`；报告会写 `missing_invoice_lines`。

## 4. 覆盖口径

`coverage` 分开报告，不合成一个百分比：

- `record`：共同支持的行数 / 有金额的账单行数；
- `amount`：`matched_postings / invoice_total`，带 `comparable` /
  `not_comparable` 和原因；
- `gross_net`：gross invoice total、net invoice total、已应用与未应用 credit。

存在负 credit/税行或 net denominator 非正时，金额覆盖显示
`not_comparable`，并保留 gross/net，不制造假性 >100%。

## 5. 完成轴与证据裁决

报告保留四个独立完成轴：

```
finance_status / verification_status / delivery_status / economics_status
```

另列证据裁决：

```
Rule Repriced / Invoice Reconciled / Vendor Reconciled / Buyer Verified /
Delivery Accepted -> PASS | FAIL | UNKNOWN | UNPROVABLE | not_applicable
```

`finance_status=complete` 只表示资料与归集轴闭合，不表示差额为零、供应商正确
或可以付款。`Delivery Accepted=FAIL` 是已裁决的失败，不把 `delivery_status`
变成 partial；没有 outcomes source 时，其余三轴是 `not_applicable`，有
outcomes source 但零行时是 `partial`。

## 6. credit / tax / refund

账单可选列 `Adjustment Type` / `Adjustment State` 映射为
`adjustment_type` / `adjustment_state`：

- `tax` + `applied`：进入 known adjustments，不推断税率；
- `credit` + `applied`：减少 amount due，但不报告为 realized savings；
- `credit`/`refund` + `issued`/`pending`/`unapplied`/`void`：排除在 invoice
  total 之外，进入 open exposure；
- 负金额但没有明确 adjustment type：进入 residual，并保留
  `unclassified` 风险，不猜成 credit。

`realized_savings.amount` 固定为 `0.00`，`claims` 为空；只有实际应用且有独立
证据的 credit/refund 才能进入后续 V-04 的版本对照，而不是本报告。

## 7. 审核门禁

`close review` 必须带 `--by`（缺失即 `INPUT_REQUIRED`，exit 2），记录：

- `reviewer`、`reviewed_at`、`attestation=attested_by_declaration`；
- `approved_scope`、`reservations`、`next_review`；
- `signoff.state=not_signed`、`payment_authorization=false`；
- 当时的 unresolved items 快照。

复核不会把任何 `unresolved` 改成 `closed`，也不会自动签核。报告审核区显示
`reviewed_not_signed`；V-04 才处理正式签核、修订和后续 credit/refund 版本。
一旦根版本存在 `signoff.json`，`close review/calculate/report` 一律拒绝，
必须走 `close revise`；签核后的报告与 signoff 字节不再被原地改写。

## 8. 重放与限制

`calculation.json` 保存 canonical 快照 hash、period-job / mapping-confirmed /
rule-map 的输入 hash、规则版本、代码/算法版本、四轴和完整 canonical outcome。
`report/manifest.json` 保存输入 hash、规则版本、算法版本、
`calculation_fingerprint`、`output_snapshot_fingerprint` 和各报告文件 hash。
重新 calculate/report 在输入不变时得到相同 `output_snapshot_fingerprint`
（报告中的 `generated_at` 不参与指纹）。period-job、mapping-confirmed 或
calculation 内容与记录不符时，report 报 `STALE_SOURCE`，不静默出数。

限制会写入 `limitations`：

- 单 vendor/account/currency；不做 FX 换算或跨币种合计；
- invoice/OCR 文档保持 unreviewed/unbooked；
- tax/credit/refund 不自动推导；
- 规则适用性由人工确认，报告不替人签规则；
- 报告不是记账凭证、付款授权、供应商争议结论，也不代表供应商多收费。

## 9. AC 对照

| AC | 落实 |
| --- | --- |
| V16 | 单条开票费用没有第二来源时 `Invoice Reconciled=UNPROVABLE`，`Rule Repriced` 仍可独立 PASS；finance 可 complete 但 open difference 保留 |
| V17 | 买方用量符合规则、供应商账单不同：保留差额、`finance_status!=complete` |
| V18 | 金额相等的两份核对通过但规则复算失败：`Invoice Reconciled=PASS`、`Rule Repriced=FAIL`、`Buyer Verified=UNPROVABLE` |
| V19 | 买方验证交付 FAIL：`Delivery Accepted=FAIL`，`delivery_status=complete` |
| V20 | 无 outcomes source 三轴 `not_applicable`；有 source 但零行 `partial` |
| V21 | 同一行多个 findings，只按行计一次；findings 金额不求和 |
| V22 | 负 credit、正 tax、未应用 refund 都不进入 realized savings；未应用项是 open exposure |
| V23 | 每个金额有 line/rule lineage；manifest 记录输入 hash、规则版本、算法版本和稳定输出指纹；超范围写入 limitations |
