# -*- coding: utf-8 -*-
"""Money Contract v1 属性测试（V-2，review 2026-11-09）。

冻结的分叉样例：仓库曾有 3 种舍入策略并存——HALF_UP（task_economics）、
ROUND_DOWN（分摊取整）、默认 HALF_EVEN（optimization/cost_drivers 展示）。
本套把契约逐条钉死：HALF_EVEN 在 .xx5 边界给出不同结果的那一类输出，
从今往后在本套件里就是失败。
"""
from __future__ import annotations

import unittest
from decimal import Decimal, ROUND_HALF_EVEN

from verovo_healthcheck import money


class MoneyContractTests(unittest.TestCase):
    # -- 契约 1：两位小数字符串，往返无损 ---------------------------- #

    def test_money_text_shape(self):
        for good in ("0.00", "12.34", "-5.67", "123456.78"):
            self.assertTrue(money.is_money_text(good), good)
        for bad in ("12.3", "12.345", "1,234.00", "$12.34", "12", "", "1e2.00"):
            self.assertFalse(money.is_money_text(bad), bad)

    def test_roundtrip_is_lossless(self):
        for raw in ("0", "0.005", "-0.005", "12.345", "999.994", "0.125", "-3"):
            value = money.parse_exact(raw)
            text = money.display(value)
            self.assertTrue(money.is_money_text(text), (raw, text))
            self.assertEqual(text, money.display(money.parse_exact(text)))

    # -- 契约 2：展示舍入 = HALF_UP；HALF_EVEN 分叉类被冻结为失败 ----- #

    def test_display_rounding_is_half_up(self):
        self.assertEqual("0.13", money.display(Decimal("0.125")))
        self.assertEqual("-0.13", money.display(Decimal("-0.125")))
        self.assertEqual("1.24", money.display(Decimal("1.235")))
        # 这正是 HALF_EVEN 会给出不同答案的边界族（0.125→0.12）：
        half_even = Decimal("0.125").quantize(money.CENTS, rounding=ROUND_HALF_EVEN)
        self.assertNotEqual(money.display(Decimal("0.125")), str(half_even))

    def test_parse_strict_vs_lenient(self):
        with self.assertRaises(money.MoneyError):
            money.parse_exact("abc")
        with self.assertRaises(money.MoneyError):
            money.parse_exact("NaN", field="cost")
        self.assertEqual(Decimal("0"), money.parse_lenient("abc"))
        self.assertEqual(Decimal("1.5"), money.parse_lenient("1.5"))

    # -- 契约 3：最大余数法分摊恒等式 -------------------------------- #

    def test_allocate_identity_holds(self):
        cases = [
            (Decimal("100.00"), {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}),
            (Decimal("0.01"), {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")}),
            (Decimal("10.00"), {"a": Decimal("1"), "b": Decimal("0"), "c": Decimal("2")}),
            (Decimal("99.99"), {"a": Decimal("7"), "b": Decimal("3")}),
            (Decimal("0.03"), {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1"), "d": Decimal("1")}),
            (Decimal("-6.00"), {"a": Decimal("1"), "b": Decimal("2")}),
        ]
        for total, weights in cases:
            with self.subTest(total=str(total), weights={k: str(v) for k, v in weights.items()}):
                allocated = money.allocate(total, weights)
                self.assertEqual(total, sum(allocated.values(), Decimal("0")))
                for task, value in allocated.items():
                    self.assertTrue(money.is_money_text(money.display(value)), (task, value))
                    if weights[task] == 0:
                        self.assertEqual(Decimal("0.00"), value)  # 零权重不承担
                if total > 0:
                    self.assertFalse(any(v < 0 for v in allocated.values()))
                else:
                    # 贷项（负总额）：分摊应为非正，不得出现正负混杂
                    self.assertFalse(any(v > 0 for v in allocated.values()))

    def test_allocate_rejects_zero_weights(self):
        with self.assertRaises(money.MoneyError):
            money.allocate(Decimal("1.00"), {"a": Decimal("0")})


if __name__ == "__main__":
    unittest.main()
