"""Money Contract v1 (V-2, review 2026-11-09).

全链路金额不变量的单一定义处。此前仓库内存在 6 处独立的 `_dec`/`_money`
助手、3 种舍入策略并存（HALF_UP / DOWN / 默认 HALF_EVEN），"金额=分单位
精确、单一舍入口径"只靠数据流惯例维持。本模块把惯例变成契约：

不变量（属性测试逐条钉住，见 healthcheck/tests/test_money_contract.py）：
1. 内部表示 Decimal；对外表示两位小数字符串（``-?\\d+\\.\\d{2}``），
   parse(text) 往返无损、无浮点中间态。
2. 展示/入库舍入 = ROUND_HALF_UP（task_economics 口径）。.xx5 边界上
   HALF_EVEN 会给出不同结果——该分叉类已被测试冻结为「不允许」。
3. 分摊 = 最大余数法：分摊之和恒等于总额（不丢分、不造分），
   零权重不承担费用，负分摊不出现。

v1 边界（诚实声明）：只迁移 task_economics 为消费方（行为零变化，由其
现有测试与 audit-case golden 证明）。optimization/cost_drivers 仍用本地
HALF_EVEN 展示——在输入恒为两位小数时与 HALF_UP 等价；v2 统一迁移时须
附 golden-case diff，不得静默改数。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, Mapping

CENTS = Decimal("0.01")
MONEY_TEXT = re.compile(r"^-?\d+\.\d{2}$")


class MoneyError(ValueError):
    """金额违反契约：不可解析、非有限、或文本不是两位小数。"""


def is_money_text(value: str) -> bool:
    """对外金额文本必须是两位小数字符串（"12.34"，不带货币符号/千分位）。"""
    return bool(MONEY_TEXT.match(str(value or "")))


def parse_exact(value, *, field: str = "amount") -> Decimal:
    """严格解析：非法/非有限输入抛 MoneyError（缺数是错误，不是 0）。"""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError):
        raise MoneyError("%s is not a valid number: %r" % (field, value))
    if not number.is_finite():
        raise MoneyError("%s must be finite" % field)
    return number


def parse_lenient(value) -> Decimal:
    """宽松解析（展示层口径，对齐 optimization/cost_drivers 现行为）：
    非法/非有限 → 0。仅用于已有容错语义的读取路径，不用于账务计算。"""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError):
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def quantize_cents(value: Decimal) -> Decimal:
    """金额舍入到分：ROUND_HALF_UP（契约第 2 条）。"""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def display(value: Decimal) -> str:
    """对外两位小数字符串。"""
    return str(quantize_cents(value))


def allocate(total: Decimal, weights: Mapping[str, Decimal]) -> Dict[str, Decimal]:
    """最大余数法分摊：sum(allocated) == total 恒等（契约第 3 条）。

    权重全零抛 MoneyError；向下取整后按余数大小补分；零权重不承担费用。
    """
    tasks = list(weights)
    denominator = sum(weights.values(), Decimal("0"))
    if denominator <= 0:
        raise MoneyError("allocation weights sum to zero")
    total = quantize_cents(total)
    exact = {task: total * weights[task] / denominator for task in tasks}
    allocated = {task: value.quantize(CENTS, rounding=ROUND_DOWN)
                 for task, value in exact.items()}
    cents = int((total - sum(allocated.values())) * 100)
    ranked = sorted(tasks, key=lambda task: exact[task] - allocated[task], reverse=True)
    for task in ranked[:cents]:
        allocated[task] += CENTS
    return allocated
