import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import tool_value


def task(task_id, group, success):
    return {"task_id": task_id, "group": group, "success": success,
            "cost": "1.0", "retry_cost": "0"}


class ToolValueUncertaintyTest(unittest.TestCase):
    def analyze(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "tasks.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return tool_value.analyze(tool_value.load_tasks(path))

    def test_rates_and_difference_have_wilson_newcombe_intervals(self):
        rows = [task("t%d" % n, "treatment", "true") for n in range(30)]
        rows += [task("c%d" % n, "control", "true" if n < 15 else "false") for n in range(30)]
        result = self.analyze(rows)
        self.assertEqual(2, len(result["treatment"]["success_rate_ci95_wilson"]))
        self.assertEqual(2, len(result["control"]["success_rate_ci95_wilson"]))
        interval = result["difference"]["success_rate_ci95_newcombe"]
        self.assertGreater(interval[0], 0)
        self.assertLess(interval[1], 1)
        self.assertFalse(result["difference"]["interval_covers_zero"])
        self.assertEqual("not_assessed", result["selection_bias"]["population_representativeness"])

    def test_small_sample_interval_keeps_uncertainty_visible_without_winner(self):
        result = self.analyze([
            task("t1", "treatment", "true"), task("c1", "control", "false"),
        ])
        self.assertEqual("insufficient_evidence", result["status"])
        interval = result["difference"]["success_rate_ci95_newcombe"]
        self.assertLess(interval[0], 0)
        self.assertGreater(interval[1], 0)
        self.assertTrue(result["difference"]["interval_covers_zero"])
        self.assertEqual("no_automatic_winner", result["difference"]["interpretation"])

    def test_missing_arm_has_unknown_interval_not_zero_success(self):
        result = self.analyze([task("t1", "treatment", "true")])
        self.assertEqual("no_control", result["status"])
        self.assertIsNone(result["control"]["success_rate_ci95_wilson"])
        self.assertNotIn("difference", result)

    def test_render_includes_uncertainty_and_selection_boundary(self):
        result = self.analyze([
            task("t1", "treatment", "true"), task("c1", "control", "false"),
        ])
        text = tool_value.render(result)
        self.assertIn("Newcombe", text)
        self.assertIn("选择偏差", text)


if __name__ == "__main__":
    unittest.main()
