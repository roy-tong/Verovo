import copy
import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import spend_action


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "examples" / "audit-golden-case"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class SpendActionGoldenCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load(BUNDLE / "audit-case.json")
        cls.manifest = load(BUNDLE / "manifest.json")
        cls.result = spend_action.build_action_list(
            cls.case, BUNDLE, cls.manifest
        )
        cls.rows = {
            (row["source"]["path"], row["source"]["line_id"]): row
            for row in cls.result["actions"]
            if row["source"]["kind"] == "invoice_line"
        }

    def row(self, line_id, path="invoices/invoice-2026-10.json"):
        return self.rows[(path, line_id)]

    def test_golden_case_separates_clean_open_unreviewed_and_settled_lines(self):
        expected = {
            "line-001": "review_required",
            "line-002": "unreviewed",
            "line-003": "settled",
            "line-004": "ready_for_payment",
            "line-005": "review_required",
            "line-006": "review_required",
            "line-007": "evidence_required",
            "line-008": "review_required",
            "line-009": "ready_for_payment",
        }
        self.assertEqual(
            {line_id: self.row(line_id)["action"] for line_id in expected},
            expected,
        )
        self.assertEqual(
            self.row("eur-line-001", "invoices/invoice-2026-10-eur.json")["action"],
            "evidence_required",
        )

    def test_only_explicitly_clean_lines_are_ready_for_payment(self):
        self.assertEqual(self.row("line-002")["finding_ids"], [])
        self.assertEqual(self.row("line-002")["action"], "unreviewed")
        self.assertIn("not explicitly confirmed clean", self.row("line-002")["reason"])
        self.assertEqual(self.row("line-004")["owner_role"], "accounts_payable")

    def test_amounts_use_decimal_strings_and_currencies_stay_separate(self):
        self.assertEqual(self.row("line-004")["amount"]["value"], "100.00")
        self.assertEqual(self.row("line-009")["amount"]["value"], "20.00")
        amounts = {
            (row["action"], row["currency"]): row
            for row in self.result["summary"]["amounts"]
        }
        self.assertEqual(
            amounts[("ready_for_payment", "USD")]["known_value"], "120.00"
        )
        self.assertEqual(
            amounts[("evidence_required", "USD")]["unknown_count"], 0
        )
        self.assertEqual(
            self.result["summary"]["case_level_amounts"][0]["unknown_count"], 1
        )
        self.assertEqual(
            amounts[("evidence_required", "EUR")]["known_value"], "90.00"
        )

    def test_case_level_outcome_is_an_evidence_action_without_an_amount(self):
        outcome = next(
            row for row in self.result["actions"]
            if row["source"]["kind"] == "finding"
        )
        self.assertEqual(outcome["source"]["finding_id"], "F-007")
        self.assertEqual(outcome["action"], "evidence_required")
        self.assertEqual(outcome["owner_role"], "buyer_operations")
        self.assertIsNone(outcome["amount"])

    def test_summary_counts_and_decision_boundary(self):
        self.assertEqual(
            self.result["summary"]["counts"],
            {
                "evidence_required": 3,
                "ready_for_payment": 2,
                "review_required": 4,
                "settled": 1,
                "unreviewed": 1,
            },
        )
        self.assertFalse(self.result["executes_payment"])
        self.assertTrue(self.result["buyer_decision_required"])
        self.assertEqual(self.result["schema"], "verovo.spend-action-list/0.1")

    def test_markdown_names_actions_owners_and_decision_boundary(self):
        markdown = spend_action.action_markdown(self.result)
        self.assertIn("ready_for_payment", markdown)
        self.assertIn("accounts_payable", markdown)
        self.assertIn("line-007", markdown)
        self.assertIn("does not execute or authorize payment", markdown)


class SpendActionValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "invoice.json").write_text(json.dumps({
            "lines": [{
                "line_id": "line-1",
                "charge_id": "charge-1",
                "amount": 10,
                "currency": "USD",
            }],
        }), encoding="utf-8")
        self.case = {
            "case_id": "case-1",
            "buyer": "Buyer",
            "vendor": "Vendor",
            "period": {"start": "2026-10-01", "end": "2026-10-31"},
            "coverage": {
                "verified_amount": None,
                "unverified_amount": None,
                "currency": "USD",
            },
            "findings": [],
            "status": "open",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_amount_is_refused_not_zero(self):
        invoice = load(self.root / "invoice.json")
        del invoice["lines"][0]["amount"]
        (self.root / "invoice.json").write_text(json.dumps(invoice), encoding="utf-8")
        with self.assertRaisesRegex(spend_action.SpendActionError, "amount"):
            spend_action.build_action_list(
                self.case,
                self.root,
                {"sources": {"invoices": ["invoice.json"]}},
            )

    def test_invoice_path_cannot_escape_case_root(self):
        with self.assertRaisesRegex(spend_action.SpendActionError, "escapes"):
            spend_action.build_action_list(
                self.case,
                self.root,
                {"sources": {"invoices": ["../outside.json"]}},
            )

    def test_invalid_top_level_case_is_refused(self):
        case = copy.deepcopy(self.case)
        del case["case_id"]
        with self.assertRaisesRegex(spend_action.SpendActionError, "case_id"):
            spend_action.build_action_list(case, self.root, {})


if __name__ == "__main__":
    unittest.main()


class SpendActionCliTests(unittest.TestCase):
    def test_cli_writes_json_and_markdown(self):
        import contextlib
        import io
        from verovo_healthcheck.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "actions.json"
            markdown_path = Path(tmp) / "actions.md"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = main([
                    "spend-actions",
                    "--case", str(BUNDLE / "audit-case.json"),
                    "--json", str(json_path),
                    "--markdown", str(markdown_path),
                ])
            self.assertEqual(rc, 0)
            result = load(json_path)
            self.assertEqual(result["schema"], "verovo.spend-action-list/0.1")
            self.assertIn("ready_for_payment", markdown_path.read_text(encoding="utf-8"))
            self.assertIn("does not execute or authorize payment", output.getvalue())

    def test_cli_json_only_and_bad_input(self):
        import contextlib
        import io
        from verovo_healthcheck.cli import main

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main([
                "spend-actions",
                "--case", str(BUNDLE / "audit-case.json"),
                "--json-only",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(output.getvalue())["case_id"], "audit-golden-2026-10")

        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            rc = main(["spend-actions", "--case", "/does/not/exist.json"])
        self.assertEqual(rc, 2)
        self.assertIn("error:", error.getvalue())
