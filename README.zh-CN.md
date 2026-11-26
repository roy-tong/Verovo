# Verovo

<img src="assets/verovo-logo-card.png" alt="Verovo" width="300">

**发现那些你原本不知道的经济问题。**

Verovo 是面向 AI Agent 的独立经济度量产品。它帮助开发者、AI 团队、财务和采购发现异常支出、独立核验供应商账单，并比较不同工具、模型和工作流获得合格业务结果的真实成本。

[OVO 统一官网](https://roy-tong.github.io/ovo/) · [English](README.md) · [买方指南](docs/buyer-review.md) · [账期重放示例](https://roy-tong.github.io/Verovo/examples/replayable-period.html)

[![CI](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml/badge.svg)](https://github.com/roy-tong/Verovo/actions/workflows/conformance.yml)
[![PyPI](https://img.shields.io/pypi/v/verovo)](https://pypi.org/project/verovo/)
[![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

## 平台能告诉你收了多少，Verovo 核验这些投入值不值

Agent 平台、网关和可观测工具已经能展示用量与价格。买方仍然需要独立答案：哪一次重试没有创造价值，哪一行账单需要补证，哪个工具能用合理成本交付一个被接受的任务。

Verovo 连接 Agent 日志、供应商记录和独立验收的业务结果，同时把财务正确性、控制、交付和价值保持为不同结论，而不是压成一个不透明的总分。

### 三种经济度量服务

| 服务 | 最小输入 | 业务交付 |
| --- | --- | --- |
| **Agent Spend Healthcheck** | 可访问的 Agent 日志和用量记录 | 费用分布、重试、异常，以及低效支出对应的任务 |
| **Independent Bill Audit** | 供应商账单和买方侧记录 | 重算费用、可支持差额、未决金额和可用于争议处理的证据 |
| **Agent Economics Optimization** | 真实任务、成本和验收结果 | 工具/模型比较、受控实验，以及优化结果的独立复核 |

一个有用的首次结果很直接：**“我发现了一个此前看不见的经济问题。”** 如果没有异常，在明确边界内给出可靠核验，同样是一种有效交付。

## 从一笔费用走到经济决定

1. **发现真实使用环境。** 识别支持的本地 Agent Runtime 及其记录能证明什么；订阅用量不自动等于边际现金账单。
2. **计算任务经济性。** 连接 Task、Operation 和 Attempt 身份，修订不重复计数，不同币种不强行相加。
3. **定位异常。** 找出异常费用、重试循环、孤儿调用和证据缺口。
4. **独立核验。** 使用明确的供应商规则，分别呈现已确认、估算和未决金额。
5. **比较并复核。** 运行可比任务，比较每个合格结果的成本，在宣布方案更好之前先检查质量。

Verovo 不需要再做一个模型网关。现有网关和 Runtime 负责执行控制，Verovo 检查控制范围、回读和后续证据。

## 证据来自度量，而不是更大的看板

Verovo 起源于对 **110+ 个仓库**的公开测量，形成了 **65+ 条证据支持的发现，覆盖 5 类重复计费问题**。这些复现证明技术度量能力，不代表客户已经节省费用或付费采用。

另一个 **4.50 美元的合成计费差异**说明历史价格规则为什么必须进入计算链。它是测试夹具，不是客户损失，也不是实际追回金额。

[阅读 Token-Accounting Bug Report](campaigns/audit-report-2026-09.md) · [查看主张与复现](https://roy-tong.github.io/Verovo/claims.html)

## 运行第一次费用检查

```bash
pipx install verovo
verovo review --sample --html buyer-review.html --json buyer-review.json
```

这个离线合成复核用本地观察对照明确的费用与交付标准。继续查看任务经济性：

```bash
git clone https://github.com/roy-tong/Verovo.git && cd Verovo
python3 healthcheck/verovo economics \
  --case examples/task-economics.json \
  --json economics.json --markdown economics.md
```

完整经济性尚不成立时，结果仍保留已归因子 total。未知成本、结果和 ROI 保持未知，不会被写成零。

## 从费用检查走到优化复核

```text
Agent 日志 / 供应商账单
    ↓
统一的用量、成本与结果记录
    ↓
异常或独立核验差额
    ↓
可比的工具、模型或工作流实验
    ↓
独立质量与成本复核
    ↓
保留 / 回滚 / 无法判断
```

潜在节省与已实现节省是两个结果。实验成功但没有观察到节省，也是一种诚实结论。

## 当前能力与下一步证据

- **当前可用：** 本地 Runtime 发现、持续费用监测、异常记录、预算建议与控制回执、独立买方复核、逐行处置、任务经济性、受控本地实验和 Proof 重放。
- **下一步验证：** 授权生产控制、完整后续费用对账、一个真实供应商账期，以及质量不下降的实际优化。
- **采用目标：** 让开发者、技术负责人和专业复核者持续独立使用，而不是增加更多费用图表。

实现边界见[功能状态](docs/feature-status.md)和[验证路线图](ROADMAP.md)。OTel、Langfuse、LiteLLM 和评测执行器是集成分工对象，不代表所有连接器都已发布。

## 继续了解

- [买方复核指南](docs/buyer-review.md)
- [任务经济性](docs/task-economics.md)
- [支持的账单复算](examples/showcase/README.md)
- [Buyer Ledger](buyer-ledger/README.md)
- [Provider SDK](sdk/README.md)
- [路线图](ROADMAP.md) · [贡献指南](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md)

Verovo 以 MIT 协议开源。通过[买方任务页](https://roy-tong.github.io/Verovo/first-look.html#contact)带来一个 Agent 环境、一个供应商账期和一项决定，或加入 [Discord](https://discord.gg/UqrM8MCXb)。
