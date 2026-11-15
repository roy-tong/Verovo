"""V-01 对抗回归：observed_difference 与原因资格必须分离（AC V01 A–F）。

These tests run the ai-invoice-reconciliation example as a subprocess inside a
throwaway copy of its directory, so the committed fixtures are never touched
and the tests exercise exactly what a user runs (`python3 reconcile.py`).

冻结反例背景（修复前的缺陷）: the script stamped every unexplained residual
with a fixed `reason_code=price_tier_mismatch` and every cost-export gap with
`cache_read_rounded_to_zero`, while the only correctness check was an identity
that holds by definition (residual := invoice - known lines). 仅凭金额差不能
确定根因；这些测试先红后绿。
"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLE_DIR = (Path(__file__).resolve().parents[1]
               / "examples" / "ai-invoice-reconciliation")


def run_example(mutate=None):
    """Copy the example into a temp dir, optionally mutate inputs, run it."""
    tmp = tempfile.TemporaryDirectory()
    workdir = Path(tmp.name) / "example"
    shutil.copytree(EXAMPLE_DIR, workdir)
    for name in ("reconciliation.csv", "reconciliation.json",
                 "line-checks.csv", "sample-report.md"):
        (workdir / name).unlink(missing_ok=True)
    if mutate is not None:
        mutate(workdir)
    proc = subprocess.run([sys.executable, "reconcile.py"], cwd=workdir,
                          capture_output=True, text=True, timeout=60)
    return proc, workdir, tmp


def drop_ground_truth(workdir):
    """AC A/B 前置：拿走一切合成 ground-truth 声明，只剩金额事实。"""
    (workdir / "ground-truth.json").unlink(missing_ok=True)


def shrink_invoice_usage_line(workdir):
    """AC F 前置：把 invoice 的 usage 行调低 500，制造负 residual 与表面 >100% 覆盖率。"""
    path = workdir / "invoice.csv"
    text = path.read_text(encoding="utf-8").replace("38462.06", "37962.06")
    path.write_text(text, encoding="utf-8")


def inflate_one_cost_row(workdir):
    """AC E 前置：篡改 cost.csv 首行 +1.00。

    总额恒等式在该篡改下依然成立（cache_gap 按定义吸收差额），只有行级
    独立核对能抓住——这正是恒等式不得作为唯一正确性证明的反例。
    """
    path = workdir / "cost.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0]["cost_usd"] = f"{float(rows[0]['cost_usd']) + 1.00:.2f}"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "team", "cost_usd"])
        writer.writeheader()
        writer.writerows(rows)


class DifferenceQualificationTest(unittest.TestCase):
    """AC V01 A–D：差额是事实，原因是分层资格。"""

    def load(self, workdir):
        return json.loads(
            (workdir / "reconciliation.json").read_text(encoding="utf-8"))

    def diff(self, doc, diff_id):
        return next(d for d in doc["observed_differences"]
                    if d["id"] == diff_id)

    def test_A_amount_only_difference_stays_unresolved(self):
        proc, workdir, tmp = run_example(mutate=drop_ground_truth)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        residual = self.diff(doc, "unexplained_residual")
        # 只有数值差 → 资格必须 unresolved，不得输出已确定根因
        self.assertEqual(residual["qualification"], "unresolved")
        for explanation in residual["explanations"]:
            self.assertNotEqual(
                explanation["confidence_kind"], "confirmed",
                "amount-only difference must not carry a confirmed cause")
        # CSV 投影同样不得出现固定根因资格
        with (workdir / "reconciliation.csv").open(encoding="utf-8") as fh:
            rows = {r["line"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows["unexplained_residual"]["qualification"],
                         "unresolved")
        tmp.cleanup()

    def test_golden_fixture_values_are_frozen(self):
        # 预先写死期望值：不靠反算 residual 自证，改动生成器或价格表会立刻红。
        self.assertFalse((EXAMPLE_DIR / "ground-truth.json").exists(),
                         "the published demo must not ship a ground-truth file")
        proc, workdir, tmp = run_example()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        self.assertEqual(doc["totals"]["recalc_total"], "38462.06")
        self.assertEqual(doc["totals"]["cost_total"], "31207.33")
        self.assertEqual(doc["totals"]["cache_gap"], "7254.73")
        self.assertEqual(doc["totals"]["invoice_total"], "26563.25")
        self.assertEqual(doc["totals"]["unexplained_residual"], "144.23")
        self.assertEqual(doc["line_checks"]["rows"], 90)
        self.assertEqual(doc["line_checks"]["matched"], 90)
        self.assertEqual(doc["line_checks"]["mismatched"], 0)
        residual = self.diff(doc, "unexplained_residual")
        self.assertEqual(residual["qualification"], "unresolved")
        self.assertEqual(residual["status"], "UNPROVABLE")
        self.assertEqual(residual["root_cause"], "unknown")
        self.assertEqual(residual["amount_usd"], "144.23")
        cache_gap = self.diff(doc, "cache_gap")
        self.assertEqual(cache_gap["status"], "UNPROVABLE")
        self.assertEqual(cache_gap["root_cause"], "unknown")
        tmp.cleanup()

    def test_B_confirmed_explanation_requires_declared_evidence(self):
        # 有显式 synthetic ground truth 声明 → 允许 confirmed，但必须标注
        # synthetic_ground_truth 且给出 basis_refs，不得冒充真实独立证据。
        # 发布样例不随附 ground-truth.json，这里显式复制示例声明来启用该路径。
        def with_ground_truth(workdir):
            (workdir / "ground-truth.json").write_text(
                (workdir / "ground-truth.example.json").read_text(encoding="utf-8"),
                encoding="utf-8")
        proc, workdir, tmp = run_example(mutate=with_ground_truth)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        residual = self.diff(doc, "unexplained_residual")
        self.assertEqual(residual["qualification"], "confirmed")
        confirmed = [e for e in residual["explanations"]
                     if e["confidence_kind"] == "confirmed"]
        self.assertTrue(confirmed, "declared ground truth must surface a "
                                   "confirmed explanation")
        self.assertEqual(confirmed[0]["hypothesis"], "price_tier_mismatch")
        self.assertTrue(confirmed[0].get("synthetic_ground_truth"))
        self.assertTrue(confirmed[0]["basis_refs"])
        self.assertIn("synthetic", (workdir / "sample-report.md")
                      .read_text(encoding="utf-8").lower())
        tmp.cleanup()

    def test_C_cache_gap_is_multi_candidate_never_fixed(self):
        # AC C：cache gap 可能是舍入/时间窗/异步修订 → 只能多候选假设，
        # 即使存在 ground-truth 声明也不得固定归因（声明未覆盖 cache gap）。
        proc, workdir, tmp = run_example()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        cache_gap = self.diff(doc, "cache_gap")
        hypotheses = {e["hypothesis"] for e in cache_gap["explanations"]}
        self.assertGreaterEqual(
            len(hypotheses), 2,
            "cache gap must keep at least two candidate explanations")
        self.assertIn("cache_read_omitted_from_export", hypotheses)
        self.assertNotEqual(hypotheses, {"cache_read_omitted_from_export"},
                            "rounding must not be the only candidate")
        for explanation in cache_gap["explanations"]:
            self.assertEqual(explanation["confidence_kind"], "heuristic")
        self.assertNotEqual(cache_gap["qualification"], "confirmed")
        self.assertEqual(cache_gap["status"], "UNPROVABLE")
        self.assertEqual(cache_gap["root_cause"], "unknown")
        tmp.cleanup()

    def test_D_multiple_candidates_keep_difference_open(self):
        # AC D：多候选并存 → 差额仍显示未闭合。
        proc, workdir, tmp = run_example()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        cache_gap = self.diff(doc, "cache_gap")
        self.assertGreaterEqual(
            len(cache_gap["explanations"]), 2)
        self.assertFalse(cache_gap["closed"])
        report = (workdir / "sample-report.md").read_text(encoding="utf-8")
        self.assertIn("open", report.lower())
        tmp.cleanup()


class LineLevelEvidenceTest(unittest.TestCase):
    """AC E：金额平衡断言不得作为唯一正确性证明。"""

    def load(self, workdir):
        return json.loads(
            (workdir / "reconciliation.json").read_text(encoding="utf-8"))

    def test_line_level_checks_across_three_sources(self):
        proc, workdir, tmp = run_example()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        checks_path = workdir / "line-checks.csv"
        self.assertTrue(checks_path.exists(),
                        "row-level cross-checks must be a first-class output")
        with checks_path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        cost_rows = list(csv.DictReader(
            (workdir / "cost.csv").open(encoding="utf-8")))
        self.assertEqual(len(rows), len(cost_rows))
        for row in rows:
            # 两个独立来源的行级金额必须逐行可见：usage×prices vs cost export
            self.assertIn("usage_recalc_usd", row)
            self.assertIn("cost_usd", row)
            self.assertIn(row["result"], ("match", "mismatch"))
        doc = self.load(workdir)
        self.assertEqual(doc["line_checks"]["mismatched"], 0)
        tmp.cleanup()

    def test_tampered_cost_row_is_caught_by_line_checks_not_identity(self):
        # 恒等式在该篡改下依然成立；只有行级独立核对披露 mismatch。
        proc, workdir, tmp = run_example(mutate=inflate_one_cost_row)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        self.assertEqual(doc["line_checks"]["mismatched"], 1)
        report = (workdir / "sample-report.md").read_text(encoding="utf-8")
        self.assertIn("mismatch", report.lower())
        tmp.cleanup()


class NegativeDifferenceAndCoverageTest(unittest.TestCase):
    """AC F：负差额与表面覆盖率 >100% 不得呈现为正常口径。"""

    def load(self, workdir):
        return json.loads(
            (workdir / "reconciliation.json").read_text(encoding="utf-8"))

    def test_negative_residual_marks_direction_and_not_comparable(self):
        proc, workdir, tmp = run_example(mutate=shrink_invoice_usage_line)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = self.load(workdir)
        residual = next(d for d in doc["observed_differences"]
                        if d["id"] == "unexplained_residual")
        self.assertTrue(residual["amount_usd"].startswith("-"))
        self.assertEqual(residual["direction"], "invoice_lower")
        coverage = doc["coverage"]
        self.assertGreater(float(coverage["net_pct"]), 100)
        self.assertEqual(coverage["status"], "not_comparable")
        report = (workdir / "sample-report.md").read_text(encoding="utf-8")
        self.assertIn("not_comparable", report)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
