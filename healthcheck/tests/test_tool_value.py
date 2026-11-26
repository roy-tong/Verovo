import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import tool_value


def task(task_id, group, success, cost, retry="0"):
    return {"task_id": task_id, "group": group, "success": success,
            "cost": cost, "retry_cost": retry,
            "comparable_group": "research-v1", "currency": "USD",
            "task_type": "research", "acceptance_rubric_ref": "rubric-v1",
            "success_evidence_ref": "acceptance:" + task_id,
            "cost_basis": "canonical_task_economics"}


class ToolValueTest(unittest.TestCase):
    def write(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "tasks.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                        encoding="utf-8")
        return str(path)

    def test_two_groups_report_success_and_cost_per_success(self):
        rows = [task("t%d" % n, "treatment", "true", "1.0") for n in range(30)]
        rows += [task("c%d" % n, "control", "true" if n < 15 else "false", "0.8")
                 for n in range(30)]
        tasks = tool_value.load_tasks(self.write(rows))
        payload = tool_value.analyze(tasks)
        self.assertEqual("observed", payload["status"])
        self.assertEqual(1.0, payload["treatment"]["success_rate"])
        self.assertEqual(0.5, payload["control"]["success_rate"])
        self.assertEqual(0.5, payload["difference"]["success_rate"])
        self.assertEqual("1.00", payload["treatment"]["cost_per_success"])
        self.assertEqual("1.60", payload["control"]["cost_per_success"])

    def test_incremental_value_uses_success_difference_minus_cost(self):
        rows = [task("t%d" % n, "treatment", "true", "1.0") for n in range(30)]
        rows += [task("c%d" % n, "control", "false", "0.5") for n in range(30)]
        tasks = tool_value.load_tasks(self.write(rows))
        payload = tool_value.analyze(tasks, value_per_success="10")
        self.assertEqual("10.00", payload["incremental_value"]["success_value"])
        self.assertEqual("0.50", payload["incremental_value"]["cost_difference_per_task"])
        self.assertEqual("9.50", payload["incremental_value"]["net_per_task"])

    def test_missing_control_is_no_control(self):
        rows = [task("t%d" % n, "treatment", "true", "1.0") for n in range(30)]
        tasks = tool_value.load_tasks(self.write(rows))
        payload = tool_value.analyze(tasks)
        self.assertEqual("no_control", payload["status"])
        self.assertEqual("unknown", payload["causality"])

    def test_small_groups_are_insufficient(self):
        rows = [task("t1", "treatment", "true", "1.0"),
                task("c1", "control", "false", "1.0")]
        tasks = tool_value.load_tasks(self.write(rows))
        payload = tool_value.analyze(tasks)
        self.assertEqual("insufficient_evidence", payload["status"])

    def test_mixed_currencies_are_incomparable_before_statistics(self):
        rows = [task("t1", "treatment", "true", "1.0"),
                task("c1", "control", "true", "1.0")]
        rows[0]["currency"] = "EUR"
        payload = tool_value.analyze(tool_value.load_tasks(self.write(rows)))
        self.assertEqual("incomparable", payload["status"])
        self.assertIn("CURRENCY_MISMATCH", payload["eligibility"]["reason_codes"])
        self.assertNotIn("difference", payload)

    def test_duplicate_task_in_one_arm_is_incomparable(self):
        rows = [task("same", "treatment", "true", "1.0"),
                task("same", "treatment", "true", "1.0"),
                task("c1", "control", "true", "1.0")]
        payload = tool_value.analyze(tool_value.load_tasks(self.write(rows)))
        self.assertEqual("incomparable", payload["status"])
        self.assertIn("DUPLICATE_TASK", payload["eligibility"]["reason_codes"])
        self.assertNotIn("difference", payload)

    def test_success_without_acceptance_evidence_is_incomparable(self):
        rows = [task("t1", "treatment", "true", "1.0"),
                task("c1", "control", "false", "1.0")]
        rows[0]["success_evidence_ref"] = ""
        payload = tool_value.analyze(tool_value.load_tasks(self.write(rows)))
        self.assertEqual("incomparable", payload["status"])
        self.assertIn("SUCCESS_EVIDENCE_MISSING", payload["eligibility"]["reason_codes"])

    def test_missing_cost_is_rejected(self):
        rows = [{"task_id": "t1", "group": "treatment", "success": "true"}]
        with self.assertRaises(ValueError):
            tool_value.load_tasks(self.write(rows))


if __name__ == "__main__":
    unittest.main()
