# Buyer Ledger — 买方侧统一费用账本与对账审查

> **Status: pre-M1。核心机制与合成端到端流程已测试；真实平台、真实账期与跨周会话仍未验证。**
> 本目录是独立的买方侧产品线；`product/MVP.md` 的 Provider 侧范围不受影响。
> `pipx install verovo` 覆盖 healthcheck、canonical finance 与 canonical Proof；本目录的 `python3 -m buyer_ledger` close/reproduce 仍需从源码 checkout 运行，不随该 wheel 提供。

**一句话**：把多平台账单与用量记录收进本地账本，按账户和币种分别回答“平台收了多少、按已确认价格应是多少、哪些记录还无法判断”。

## 已强制的账本纪律

| 机制 | 当前实现 |
|---|---|
| 幂等 | 同内容、同语义文档与源行重复导入不重复入账；不同账户/Agent/源行不会被误去重 |
| 原子导入 | 一份文档的全部行在一个事务内入账；中途失败可重试，不留半份数据 |
| 修订与冲销 | 修订必须递增且不可分叉/跨账户/跨平台；冲销不能超出原行，Usage 冲销必须等值反向 |
| 对账粒度 | `period + provider + account + currency`；不同账户和币种不相加 |
| 三级口径 | 账单事实、平台用量、复算估算分列；部分复算只显示小计，不出正式差额 |
| 审核阻断 | `pending / undeterminable` 记录存在时输出 `review_required`，不输出 matched 或经济差异 |
| 材料覆盖 | 已登记但未解析/待审的账期文档也阻断确定性结论；parseable 导入强制明确 YYYY-MM |
| 定价 | 只选覆盖事件日的唯一政策；免费额度/阶梯价按计费作用域聚合；同版本政策不可变；旧失效估算会撤回 |
| 快照 | 每次对账原子替换完整账期快照；重跑不会残留已失效金额 |
| 证据 | 文件哈希、SourceDocument、源行定位、采集 run/recipe/audit 元数据可下钻 |
| 工具能力目录 | 26 个国内外工具（研究语料约 110 个，目录只收编可核实条目），记录买家/卖家角色、计费模型、计量单位、账单来源、审计能力和验证级别；报告自动披露未知 provider |
| 双视角 | 买家费用核验；卖家计量完整性。未计价/未关联用量不得直接宣称漏收收入 |
| CDP 采集 | 附着用户自行启动的本地 Chrome，复用受审配方、域名/下载校验和同一入账管线；不接收密码 |

## 安装与验证

```bash
cd buyer-ledger
python3 -m unittest discover -s tests -t .     # Buyer Ledger 全量单元与回归测试

# 可选：真实浏览器采集
pip install playwright && playwright install chromium

# 可选：OpenAI 兼容 LLM，仅用于首次生成解析草案
export VEROVO_LLM_BASE_URL=... VEROVO_LLM_API_KEY=... VEROVO_LLM_MODEL=...
```

核心仅使用 Python ≥3.9 标准库。LLM 端点必须使用 HTTPS；仅本机端点允许 HTTP。

## AI invoice reconciliation（公开方法页 + 可跑样例）

- 方法页：[docs/ai-invoice-reconciliation.md](docs/ai-invoice-reconciliation.md) ——
  7 个失效模式：缓存取整、四段输入价、席位、credits/税/汇率、月末修订窗口、
  币种、共享 key 归属。
- 可跑样例：[examples/ai-invoice-reconciliation](examples/ai-invoice-reconciliation)：

```bash
cd examples/ai-invoice-reconciliation
python3 make_demo.py   # 生成合成账单 + ground-truth 注入声明（一个供应商、一个账户、一个账期）
python3 reconcile.py   # 输出 reconciliation.csv/.json + line-checks.csv + sample-report.md
```

- 合成数据，不是客户案例；差额事实与原因资格分离：无独立证据时资格默认
  `unresolved`，候选解释带 `basis_refs`/`confidence_kind=heuristic`，
  `confirmed` 仅在显式 `synthetic_ground_truth` 声明下成立（不冒充真实证据）。
- 金额平衡恒等式 `recalc + seats + credits + tax + residual = invoice_total`
  只是 sanity check（按 residual 定义恒成立）；正确性证据来自
  `line-checks.csv` 的行级独立核对（usage×价格 vs 成本导出 vs 账单行）。
- 这不是产品完整功能，是“把发票、成本导出、用量导出对到分”的最小可复现件；
  真实账期适配、PDF/XLSX、席位/承诺/汇率仍需按客户材料做。

## 可重放账期与安全公开副本

`reproduce` 把事件、已批准规则、身份映射、差异分类和重放结果收进一个目录；`verify` 校验文件哈希并重跑输入指纹、revision、计数和指标。构建先在同级临时目录完整落盘并自验，通过后才原子替换正式目录；失败不会留下半份新包，也不会改写 overwrite 前的旧包。公开演示见 [examples/replayable-period](examples/replayable-period)。

```bash
B="python3 -m buyer_ledger"
$B reproduce build --input examples/replayable-period/events.jsonl \
  --period 2026-10 --rule-map examples/replayable-period/rule-map.json \
  --out /tmp/verovo-private-period --data-classification synthetic
$B reproduce verify /tmp/verovo-private-period
$B reproduce publish /tmp/verovo-private-period \
  --out /tmp/verovo-public-period --by synthetic-fixture-reviewer
$B reproduce verify /tmp/verovo-public-period
$B reproduce proof /tmp/verovo-private-period \
  --out /tmp/verovo-proof --by proof-reviewer
```

`reproduce proof` 是现有重放包的 Prove Anything 只读投影：只输出白名单的 manifest 与聚合重放字段，不复制私密输入、原始来源路径或逐行主体；源包保持字节不变，外部结果保持 UNCHECKED。投影可用 `python3 -m ovo_acceptance proof /tmp/verovo-proof` 复核；缺自包含输入时 replay 保持 UNAVAILABLE。

默认 `data_classification=confidential`，不能直接公开。只有 `synthetic` 或打包时已由 `--sharing-approved-by` 具名批准的 `public-redacted` 包可生成公开副本；脱敏包的发布人必须与批准人一致。`publish` 只写本地目录、不上传，公开副本清除原始绝对路径，生成 `template.json`、四步 `index.html` 和 `public-share.json`。重放 PASS 不等于交付验收：Delivery 保持 UNKNOWN，买方 Decide 保持 UNPROVEN；模板新任务默认私有。

## 账单截图 / 可见文本候选（V-08）

可选入口不依赖 `invoice2data`：本地文本、macOS Vision 截图 OCR 或调用方提供的
离线 OCR 行，先生成 `unreviewed` 草稿。真实 Vision 仅支持 macOS；非 darwin
明确返回 `UNSUPPORTED_SCOPE`，可改用 `--text/--paste` 或 `--ocr-json`。

```bash
B="python3 -m buyer_ledger"
$B bill-candidate add ./bill-candidates --text bill.txt
$B bill-candidate add ./bill-candidates --image shot.png              # macOS Vision
$B bill-candidate show ./bill-candidates cand-0001                   # 查看字段证据/队列
$B bill-candidate review ./bill-candidates cand-0001 \
  --field amount --value 20.00 --by finance                          # 每个关键字段逐一确认
$B bill-candidate book ./bill-candidates cand-0001 --by finance \
  --into ./bundle --account acct-1 --usage usage-export.jsonl --rule-map contract-rules.json
```

草稿保存原始截图/文本及 SHA-256、图片 OCR 的 `ocr.txt`/`ocr.json`、字段 span/bbox
与置信度；未提取的 `vendor/invoice_no/period/currency/amount` 全为 `null`。所有关键
字段确认后才允许显式 `book`，导出既有 close 可读的 bills CSV + `period-job.json`。
坏 OCR、行项目与总额不一致、重复账单和混合币种不会假通过。该入口不自动运行通用
`recount/review`，不证明 OCR 准确或买方独立性，也不执行/授权付款或退款。完整命令见
[docs/ocr-golden-chain.md](docs/ocr-golden-chain.md)。

## 账期包导入与人工字段映射（V-02）

把一份本地账期包（`verovo.period-job/1` + bills/usage 导出 + 已批准规则表）
导成既有 Period Intake 能消费的 canonical bundle。**导入只给提议，人工确认才
生成 canonical；原始文件不改，重复导入幂等并保留原始 sha256。**

```bash
B="python3 -m buyer_ledger"

$B close init ./close-2026-10 --from-job ./bundle/period-job.json
$B close ingest ./close-2026-10 --bundle ./bundle
$B close mapping ./close-2026-10                 # 列映射 + open items 明细 + 可复制的确认命令
$B close mapping ./close-2026-10 --confirm --review-by finance \
    --control-domain usage=vendor                # 控制域未裁决时必填；预览会打印确切参数
$B close status ./close-2026-10                  # BuyerCloseCase 只读投影
```

- 半开区间 `[from, to)`、单 vendor/account/currency；跨币种先拆分，不合并成
  一个精确总额。时区与账单展示口径一致。
- 货币混合、有效期不覆盖账期的价格表、越界路径/符号链接都会被拒绝；
  重复 charge/usage 只入账一次；未审核映射不能生成 canonical。
- 预览逐条打印 open items（来源 / 字段 / 原因），并在控制域未裁决时给出带
  `--control-domain` 的完整确认命令；确认输出会点名被当作缺口接受的字段
  （记入 `pending_fields_approved_as_gaps`，计算与报告里仍是 open item）。
- vendor export 始终是 vendor-controlled（即使买方本地下载）；两份 vendor
  导出不能变成 `Buyer Verified`。invoice/OCR 只登记为 `unreviewed`、
  `unbooked` 文档，不凭文本抽取入账。
- 详细契约、错误码、目录结构与覆盖诊断见
  [docs/period-import.md](docs/period-import.md)。

## 买方可签核的月结报告（V-03）

`calculate` 运行既有 canonical 财务链，`report` 生成六份可复核/可重放的报告
对象，`review` 记录署名复核但**不签核、不授权付款、不关闭未决项**。

```bash
$B close calculate ./close-2026-10
$B close report ./close-2026-10
$B close review ./close-2026-10 --by finance-reviewer \
  --scope "read report and reserve on credit" \
  --reservation "credit note not applied" \
  --next-review 2026-11-30
```

- 六份对象：`report.md`、`report.json`、`line-reconciliation.csv`、
  `review-queue.csv`、`evidence-index.json`、`manifest.json`（后者的输入
  hash / 规则版本 / 代码与算法版本 / 输出快照指纹支持 V23 重放）。
- `report.md` 与终端摘要先回答付款/续约建议、账单与未解释差额、任务验收、任务成本和是否划算；未决事项存在时不建议直接付款或续约。内部裁决码、逐行差异和重放元数据保留在附录，不改变任何金额或裁决语义。
- 四完成轴与 PASS/FAIL/UNKNOWN/UNPROVABLE 裁决分开显示；`finance_status=
  complete` 只代表资料与归集轴闭合，报告明确写“不可用于付款”。
- 每行互斥落入 matched / explained adjustment / excluded pending
  adjustment / residual；`invoice_total = matched_postings +
  known_adjustments + open_difference`，findings 金额不重复相加。
- 负 credit、正 tax、未应用退款不报 realized savings；未应用项是 open
  exposure。缺少 invoice 行时金额是 `null`，不是 `0`。
- 详细说明见 [docs/close-report.md](docs/close-report.md)。

## 差异处置与跨产品证据交接（2026-11-18）

月结报告中的差异不会因为 `review` 或 `signoff` 自动消失。责任人必须针对报告中的
`D-N` open item 分别登记问询、更正、争议、预留或解决凭据；只有显式 `resolved`
才把该项投影为已解决，后续 `dispute` 可以重新打开。处置记录追加保存，不改写金额；
已签核版本必须新建 revision，不能原地补写。

```bash
$B close action ./close-2026-10 --open-item D-1 --type inquiry \
  --owner finance --by reviewer --source-ref ticket:FIN-123 \
  --occurred-at 2026-11-18T18:00:00+08:00
$B close action ./close-2026-10 --open-item D-1 --type resolved \
  --owner finance --by reviewer --source-ref credit-note:CN-123 \
  --note "买方已收到并核对更正凭据" --occurred-at 2026-11-19T10:00:00+08:00
$B close action ./close-2026-10 --list
```

`source_ref` 是登记人提供的本地声明，Verovo 不因此声称供应商已收到/接受，也不
代发问询、提交争议、记账、退款或授权付款。报告、`review-queue.csv` 和 manifest
会显示处置状态与日志哈希，原始计算金额保持不变。

Growovo 的执行与产品事件可以先作为**未入账证据**导入：

```bash
# 在 Growovo 仓库：python3 mkt.py grow handoff-verovo RUN_ID --out handoff.json
$B handoff import-growovo ./handoff.json --out ./growovo-evidence
```

导入校验 schema、run id、SHA-256 完整性、敏感字段与非空证据，并拒绝覆盖已有目录。
`source_integrity_verified=true` 只表示 JSON 主体未被篡改；
`receipt_verified_by_verovo=false`、`business_outcome_observed_by_verovo=false`。
它不会自动进入 canonical 账本、验收交付、确认收入或授权付款。

## 版本化月结与后续调整（V-04）

每个版本独立重算，根目录是 r1；`revise` 快照出 `revisions/r1` 后只追加
`r2/r3/...`，已签核的 canonical 与报告不被覆盖。`signoff` 只对指定版本的
指纹署名，不是付款授权，也不关闭 unresolved items。

```bash
$B close revise ./close-2026-10 --bundle ./bundle-v2 \
  --review-by finance --reason "vendor revision"
$B close revise ./close-2026-10 --source ./credit-note.csv --role bills \
  --review-by finance --reason "credit note issued"
$B close signoff ./close-2026-10 --revision r2 \
  --by buyer-controller --scope "accept v2 with open credit" \
  --reservation "credit not applied"
$B close status ./close-2026-10                # 默认最新 revision；先恢复待完成 journal
$B close status ./close-2026-10 --revision r1

# 直接 PeriodCase/build_case 投影不执行恢复：pending journal 或读取期间状态变化会拒绝，
# 不返回事务中点的已签核视图；init/ingest/mapping 写入统一经过 CaseRepository。
```

- `PeriodRevision` 记录 `period_job_id`、`revision_id`、`parent_revision_id`、
  `input_snapshot_hash`、`rule_map_hash`、`source_added/removed/changed`、
  `reviewer`、`closed_at`、`status`。
- 同输入 `revise` 直接返回父版本（`idempotent`），不生成新版本；同来源同路径
  的新内容只按新版本归集，不叠加旧版本。
- `delta` 分栏显示金额/原因/完成轴变化、新证据、已应用 credit 与仅发出未应用
  credit；issued credit 不计 realized，applied credit 减少 amount due 但仍
  不算 savings。
- 签核要求 `--by`，记录 scope/source/rule 指纹；无署名不产生 signoff，未决
  项不会被自动改成 closed。详细说明见
  [docs/period-revisions.md](docs/period-revisions.md)。

### 审计后的导入/报告边界（2026-11-15）

- `[from, to)` 越界行只进 `period_outliers` 与 open items，不进入
  `invoice_total`；重复 `charge_id` 只保留首笔，报告不再按重复 reconciliation
  行双计。
- 缺币种的行标记 `excluded_currency_unknown`，总额为 `null`，不默认 USD；
  缺 unit price/rate rule 时 `rule_repricing_status=partial`、小计为 `null`。
- 报告带 `invoice_total_status`（complete/partial/unknown）；缺 charge_id、
  重复行、越界行都会显式降低状态。
- 已签核根版本不可再 calculate/report/review；只能 `close revise`。计算结果
  绑定 period-job/mapping/rule-map 输入 hash，任一变化都会 `STALE_SOURCE`。

## 文件导入与 Agent 首次适配

```bash
B="python3 -m buyer_ledger --db ledger.db"

$B init

# 查看国内外工具支持矩阵（不会因为“已编目”就冒充已接通）
$B catalog summary
$B catalog list --region cn --role buyer
$B catalog list --region global --role seller
$B catalog show anthropic
$B catalog coverage --period 2026-10
# 字段含义与新增工具规则见 catalog/README.md

$B import bill.csv --provider X --tier bill_fact --period 2026-10 --account A

# 新版式需要 Agent 适配：只生成草案，不直接入账
$B import bill.csv --provider X --tier bill_fact --period 2026-10 --account A --agent

# 审核规格后显式批准，再解析原文件
$B rule show <fingerprint> --provider X --doc-type bill --tier bill_fact
$B rule approve <fingerprint> --provider X --doc-type bill --tier bill_fact
$B parse --document <doc-id>

# 已知导出格式预设（openai/anthropic 用量 CSV；表头命中即确定性重放，未命中仍走 --agent）
$B preset list
$B preset apply openai/usage_csv
$B import openai-usage.csv --provider openai --tier platform_usage --doc-type usage --period 2026-10
# 客户材料索取清单与逐家导出步骤见 guides/export-guides.md

$B policy policy.json
$B price --provider X --period 2026-10
$B reconcile --period 2026-10
$B report --period 2026-10 --out report.md
$B report --period 2026-10 --perspective seller --out seller-audit.md
$B show <row-id>
$B status

# 后续账期：资料和规则就绪后，一条命令完成复算、对账与报告
$B run --period 2026-10 --out report.md
```

Agent/程序边界：

- 默认**不向 LLM 发送原始数据行或文件名，仅发送表头及 provider/doc_type**；金额、数量、单价、税额禁止由 LLM 常量生成。
- 草案先经过 schema、真实表头和整份样例 dry-run，再存为 `pending`；必须显式 `rule approve` 才可重放。
- 规则按 `provider + doc_type + tier + format fingerprint` 隔离，不能跨平台或跨资料类型误用。
- 同版式后续重放不调用 LLM。重解析会核对文件哈希，源文件被替换后拒绝绑定到旧证据。
- 支持 UTF-8 CSV/TSV，单文件默认上限 20 MiB（`VEROVO_MAX_IMPORT_MB` 可调高，超限时按账期拆分）；其他编码需先无损转换。总额行从映射的金额列校验，不再按字符串长度猜数字。

## 支持案例（公开计费研究，不是客户案例）

- **扣子 Coze**：一个企业账单里同时有积分、模型、官方插件、三方插件现金、音视频和存储；积分页是预估值，最终以火山账单为准。适合验证“平台代收 + 积分 + 现金”混合账。
- **Anthropic**：Token、Web Search、Code Execution、Managed Agents session-hour 四项并列；搜索费不含处理结果的 Token。适合验证一个 Agent 任务对应多条费用。
- **高德 / 和风天气**：月配额、服务分组、阶梯价、流量包和最低计费并存。适合验证“按次/按万次 + 配额 + 阶梯”的复算。
- **Browserbase / E2B**：浏览器小时、代理流量、CPU/内存/运行秒数都是独立计量。适合验证时间型和资源型用量。
- **阿里云 OCR / 百度 OCR / AWS Textract / Azure Document Intelligence**：按成功调用或页数、免费额度、预置/自定义模型、预付费包混合。适合验证按页计费和资源包抵扣。

这些是 2026-10-29 公开价格与文档的研究样本；没有客户授权、实际账单或退款结果。

## 公开账单不满研究（探索性）

SURE 1.6 研究从 1,949 条公开发现中审读 150 条，得到 66 条严格证据；其中 credits 到期/清零 22、未预期扣费/硬上限失效 9、自动充值 8、计量不一致 6、退款受阻 5，涨价仅 2。审计 12 门中 3 门未过（仅 3/18 vendor、单一地区、反证 7 条），结论上限 **exploratory**。

这不是投诉率或市场验证。中文平台样本为 0；Coze/百炼/方舟/高德/和风不能据此下结论。发现层已扩展到 **50,000 条**唯一公开记录（HN 49,090）；严格证据仍为 66，审读仍为 150。50k 压力测试：H1 证据形状支持但提及量接近，H2 不支持，H4 关键词初筛降级率 71.6%。摘要见 [COMPLAINTS-20261029.md](COMPLAINTS-20261029.md)。

## L1 浏览器采集

```bash
$B recipe validate recipes/examples/*.json
$B recipe dryrun recipes/examples/*.json
$B session login --recipe recipe.json --account A
$B session status --recipe recipe.json --account A
$B fetch --recipe recipe.json --period 2026-10 --account A
$B audit verify <trail.jsonl>
```

- 客户在专用浏览器档案中亲手登录；同平台不同账户使用不同 profile。
- 配方限制 HTTPS 与 `allowed_hosts`，context 在导航请求前拦截越源跳转，每个动作前重查当前页；下载来源 URL 也在保存/入账前校验。
- 下载和截图只允许安全 basename，写入独立 run 目录。
- 下载记录关联 `run_id / recipe_sha256 / document_id / audit_path`；本地审计文件权限设为 `0600`，目录为 `0700`。
- 采集后若仍需解析，命令明确返回 `needs_parse`，不会把“0 条账本行”显示为完整成功。
- 反自动化或步骤失败转人工交接，不绕过防护。

### 附着现有 Chrome（CDP）

用于没有 API、但用户愿意在自己的浏览器会话中导出账单的平台。先由用户启动一个**专用** Chrome 档案并打开本地调试端口：

```bash
# macOS 示例；只在专用 profile 上使用，不要对日常浏览器启用
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.verovo/chrome-cdp"

# 用户亲手登录后，系统只检查 CDP 和设备信息
$B session cdp --endpoint http://127.0.0.1:9222

# 附着会话执行同一份受审配方，下载物仍进入账本
$B fetch --recipe recipe.json --period 2026-10 \
  --cdp http://127.0.0.1:9222 --account A

# 解析规则已批准时，下载后直接复算、对账并出报告
$B fetch --recipe recipe.json --period 2026-10 \
  --cdp http://127.0.0.1:9222 --account A --analyze --out report.md
```

- CDP endpoint 只允许 loopback，且拒绝 URL 中的账号、query、fragment；不会把调试端口暴露到网络。
- 附着时不复制密码、不代填、不关闭用户的整个浏览器；只创建并关闭本次运行自己的页面。
- `run_start` 审计记录包含 `transport=cdp`，配方、来源校验、下载来源校验和入账流程与独立 profile 相同。
- 任何能访问本地调试端口的进程都可以控制该浏览器；使用一次性专用 profile，运行后关闭。CDP 不是反自动化绕过手段。

**安全边界必须准确理解：**配方是可信代码，不是安全沙箱。`click` 可能产生服务端副作用，因此“动作白名单”不等于结构性只读。只使用经过审核的配方，并优先使用平台只读角色/专用账户。哈希链只检查审计记录内部一致性；没有签名或外部信任锚，不能证明抗主动篡改，也不是执行回放器。

## 当前验证状态

| 项目 | 状态 |
|---|---|
| 173 项单元/回归测试 | ✅ 通过，覆盖金额、账户、币种、修订、定价、解析审批、原子恢复、能力目录、双视角、浏览器运行时边界、账期包导入/字段映射、可签核月结报告与追加式 revision/signoff |
| 合成 CSV 的 CLI 导入→复算→对账→报告 | ✅ 已端到端运行，重复导入幂等 |
| 26 个工具的账单/计量维度编目 | ✅ 目录与未知 provider 披露已测试；不代表已接通 |
| CDP loopback 附着、下载与自动分析 | ✅ 本机 Chrome 156 + 本地 HTTPS 合成账单：下载→入账→`--analyze` 出报告；未做真实平台账单后台验收 |
| 3–5 个真实平台、≥1 个无 API 平台 | ⬜ 未开始 |
| 真实平台跨周会话、过期交接 | ⬜ 机制存在，未做跨周验收 |
| 真实任务 ≥80% 步骤确定性执行 | ⬜ 尚无真实运行样本和分母 |
| 真实账单金额一致性与证据被供应商接受 | ⬜ 未验证 |
| 浏览器执行回放、抗主动篡改 | ⬜ 未实现；当前仅有配方重放与审计链一致性检查 |

## 明确不在当前范围

- XLSX/PDF 原生解析、多币种汇率换算、支付/退款执行。
- 通用自动接入所有平台；每个真实平台仍需配方、条款与账户确认。
- 把平台总账强行分摊到单次 Tool 调用；无法关联项必须保留。
