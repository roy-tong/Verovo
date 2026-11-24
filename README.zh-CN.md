# Verovo

<img src="assets/verovo-logo-card.png" alt="Verovo" width="300">

**Agent 经济的独立计量与核验层。**

**让 AI 服务的每一笔钱，都花得明白。** Verovo 帮助团队核验 Agent 和工具费用，把支出连接到已验收的任务结果，并决定下一笔该付款、补证、整改还是换方案。

[官网](https://roy-tong.github.io/Verovo/) · [English](README.md) · [核验指南](docs/buyer-review.md) · [账期重放](https://roy-tong.github.io/Verovo/examples/replayable-period.html) · [Discord](https://discord.gg/UqrM8MCXb)

[![CI](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml/badge.svg)](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml)
[![PyPI](https://img.shields.io/pypi/v/verovo)](https://pypi.org/project/verovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## 核验每笔费用，度量每项结果

一张总账单回答不了哪一行要补证、一次重试是否值得，或下一项任务该用哪个工具。Verovo 把供应商规则、账单、用量、交付和买方验收连接起来，让财务、业务和工程回答三个问题：

- **这笔费用接下来怎么处理？** 按适用规则复算支持的账单，再生成逐行工作清单：可进入付款处理、待补证、待复核、未复核或已结算。
- **工作交付到位了吗？** 用买方自己的验收判据检查实际交付，不把交付失败自动等同于计费错误。
- **下一单该选哪个方案？** 连接直接与分摊任务成本、已验证结果、每次验收成功成本和明确的情景比较。

财务正确性、控制（授权）、交付和经济价值始终是独立结论。缺失证据保持可见，不会变成零或通过。

## 从一个账期，到清楚的下一步

1. **带上一个账期**：整理一家供应商的规则、账单、用量和交付记录。
2. **分别核验费用与结果**：并排查看财务、交付和证据覆盖，不混成一个综合分。
3. **分配下一项动作**：给每个未决项责任角色和原因；买方实际付款、挂起或供应商复核另行记录。
4. **重放并比较**：复现证据，用任务经济性支持下一次验收、续约或采购选择。

[查看买方核验报告](https://roy-tong.github.io/Verovo/examples/buyer-review.html) · [重放一个账期](https://roy-tong.github.io/Verovo/examples/replayable-period.html)

## 运行第一次核验

```bash
pipx install verovo
verovo review --sample --html buyer-review.html --json buyer-review.json
```

离线合成示例展示费用与交付核验，以及可追溯的判据和观察记录。按[核验指南](docs/buyer-review.md)准备自己的任务。

## 把 Audit Case 变成费用处置清单

在源码 checkout 中运行：

```bash
python3 healthcheck/verovo spend-actions \
  --case examples/audit-golden-case/audit-case.json \
  --json spend-actions.json --markdown spend-actions.md
```

合成案例输出 3 项补证、4 项复核、1 项未复核、2 项可进入付款处理和 1 项已结算。只有明确标记为 clean 的账单行才能进入付款处理；报告不执行或授权付款。

## 看清每个任务的经济性

```bash
git clone https://github.com/roy-tong/Verovo.git && cd Verovo
python3 healthcheck/verovo economics \
  --case examples/task-economics.json \
  --json economics.json --markdown economics.md
```

Verovo 连接任务、Agent 运行、用量、票据与已验证结果。比较直接与分摊成本、每次验收成功成本，以及供应商或工作流情景。成本和结果覆盖不完整时保持明确。

[任务经济性](docs/task-economics.md) · [账期摄入](docs/period-intake.md) · [成本驱动](docs/cost-optimization.md)

## 让外部组件遵守同一份证据契约

```bash
python3 healthcheck/verovo provider check \
  --manifest examples/provider-manifest/otel-file.json
```

Provider Manifest 记录引擎版本、集成方式、来源信任、准入检查、资源限额、覆盖、清理和原始快照摘要。本地准入结果分为 `admit`、`degrade`、`refuse`，不宣称已连接真实引擎或生产覆盖完整。查看 [Provider 契约](docs/provider-contract.md)。

## 导出并重放账期证明

```bash
verovo proof export --bundle healthcheck/tests/fixtures/canonical-finance --out /tmp/verovo-period-proof --by reviewer
verovo proof replay /tmp/verovo-period-proof --bundle healthcheck/tests/fixtures/canonical-finance
```

输入变化时重放返回 `EXPIRED`，财务输出变化返回 `FAIL`；两个命令都不修改账期，也不授权付款。安装版 wheel 覆盖上述 canonical Proof 命令；pre-M1 的 `python3 -m buyer_ledger` 仍是源码 checkout 工具，详见 [Buyer Ledger](buyer-ledger/README.md)。

## Verovo 与相邻工具的关系

- **OTel、OpenLIT、Langfuse** 提供执行观察。Verovo 用合格记录做独立财务与结果核验，不把 Trace 当最终账单。
- **LiteLLM 等 Gateway** 执行路由和控制。Verovo 用于检查其记录、控制覆盖和后续收费，而不是替代 Gateway。
- **FOCUS 与 FinOps 工具** 交换和汇总成本。Verovo 把金额连接到 Agent 任务、验收与采购选择。
- **Inspect 等 Runner** 执行评测。Verovo Lab 负责预注册、可比性限制和经济性解释。

这些是集成边界，不代表所有连接器都已交付。[当前能力](docs/feature-status.md)与[验证路线图](ROADMAP.md)分别说明本地功能、真实供应商集成和客户验证状态。

## 从公开测量工作中长出来

Verovo 始于一次约 110 个 AI 用量与成本工具的审计。项目公开了 65 条以上可复核发现，覆盖五类反复出现的计费问题，并持续记录复现与上游修正。

- [The Token-Accounting Bug Report](campaigns/audit-report-2026-09.md)
- [带日期的案例卡](docs/launch/README.md)
- [公开证据与复现](https://roy-tong.github.io/Verovo/claims.html)

这项工作延续在开源规则、SDK、符合性向量和买方工具中。可以使用 CLI、通过 MCP 接入、加入 [Discord](https://discord.gg/UqrM8MCXb)、关注 [X](https://x.com/elliwoodtong)，或带一个具体供应商账期到[买方任务页](https://roy-tong.github.io/Verovo/first-look.html#contact)。

## 继续探索

- [买方核验指南](docs/buyer-review.md)
- [支持的账单复算](examples/showcase/README.md)
- [Buyer Ledger](buyer-ledger/README.md)
- [Provider SDK](sdk/README.md)
- [路线图](ROADMAP.md) · [贡献指南](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

Verovo 以 MIT 开源。从一家供应商、一个账期和一个真实决定开始。
