import copy
import json
import tempfile
import unittest
from pathlib import Path

from verovo_healthcheck import spend_action


class SpendActionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.invoice_a = "invoice-a.json"
        self.invoice_b = "invoice-b.json"
        self._write_invoice(self.invoice_a, "line-1", 10, "USD", "charge-a")
        self._write_invoice(self.invoice_b, "line-1", 99, "USD", "charge-b")
        self.case = self._case([])

    def tearDown(self):
        self.temp.cleanup()

    def _write_invoice(self, path, line_id, amount, currency, charge_id):
        document = {
            "lines": [{
                "line_id": line_id,
                "charge_id": charge_id,
                "amount": amount,
                "currency": currency,
            }],
        }
        (self.root / path).write_text(json.dumps(document), encoding="utf-8")

    def _case(self, findings, currency="USD"):
        return {
            "case_id": "case-evidence",
            "buyer": "Buyer",
            "vendor": "Vendor",
            "contract_version": "contract-v1",
            "period": {"start": "2026-10-01", "end": "2026-10-31"},
            "coverage": {
                "verified_amount": None,
                "unverified_amount": None,
                "currency": currency,
            },
            "findings": findings,
            "status": "open",
        }

    def _review(self, path, line_id, digest=None):
        actual = spend_action._source_digest(self.root / path)
        return {
            "invoice_source_id": path,
            "line_id": line_id,
            "source_digest": digest or actual,
            "rule_version": "contract-v1",
            "reviewed_by": "Reviewer",
            "reviewed_at": "2026-11-01T00:00:00Z",
        }

    def _manifest(self, reviews, invoices=None, clean=None):
        manifest = {
            "sources": {"invoices": invoices or [self.invoice_a, self.invoice_b]},
            "charge_reviews": reviews,
        }
        if clean is not None:
            manifest["clean_charge_lines"] = clean
        return manifest

    def _finding(self, finding_id="F-1", amount=10, currency="USD", status="identified",
                 affected=True, settlement_ref=None, credit_path=None):
        finding = {
            "finding_id": finding_id,
            "case_id": "case-evidence",
            "classification": "duplicate_charge",
            "severity": "high",
            "amount": {
                "currency": currency,
                "value": "%.2f" % amount,
                "basis": "Duplicate charge under review.",
            },
            "status": status,
            "evidence_refs": [{
                "path": self.invoice_a,
                "record_id": "line-1",
                "field": "amount",
            }],
            "rule_ref": {
                "rule_id": "CR-1",
                "version": "1",
                "confirmed_by": "Reviewer",
                "confirmed_at": "2026-11-01T00:00:00Z",
            },
            "provable": True,
        }
        if affected:
            finding["affected_lines"] = [{
                "path": self.invoice_a,
                "line_id": "line-1",
            }]
        if settlement_ref:
            finding["settlement_ref"] = settlement_ref
        if credit_path:
            finding["evidence_refs"].append({
                "path": credit_path,
                "record_id": settlement_ref.split(":", 1)[1],
                "field": "applied",
            })
        return finding

    def _row(self, result, path):
        return next(row for row in result["actions"] if row["source"]["path"] == path)

    def _write_credit(self, amount, applied=True, charge_id="charge-a"):
        credit = {
            "credits": [{
                "credit_id": "C-1",
                "charge_id": charge_id,
                "amount": amount,
                "currency": "USD",
                "applied": applied,
                "applied_at": "2026-11-02" if applied else None,
            }],
        }
        (self.root / "credits.json").write_text(json.dumps(credit), encoding="utf-8")

    def test_same_line_id_is_ready_only_on_the_reviewed_invoice(self):
        result = spend_action.build_action_list(
            self.case, self.root, self._manifest([self._review(self.invoice_a, "line-1")])
        )
        rows = {row["source"]["path"]: row for row in result["actions"]}
        self.assertEqual(rows[self.invoice_a]["action"], "ready_for_payment")
        self.assertEqual(rows[self.invoice_b]["action"], "unreviewed")

    def test_crlf_checkout_does_not_change_source_digest(self):
        payload = json.dumps({
            "lines": [{
                "line_id": "line-1",
                "charge_id": "charge-a",
                "amount": 10,
                "currency": "USD",
            }],
        }).replace("\n", "\r\n").encode("utf-8")
        (self.root / self.invoice_a).write_bytes(payload)
        result = spend_action.build_action_list(
            self.case, self.root, self._manifest([self._review(self.invoice_a, "line-1")])
        )
        row = self._row(result, self.invoice_a)
        self.assertEqual(row["action"], "ready_for_payment")

    def test_changed_source_digest_invalidates_the_old_review(self):
        review = self._review(self.invoice_a, "line-1", digest="0" * 64)
        result = spend_action.build_action_list(
            self.case,
            self.root,
            self._manifest([review], clean=["line-1"]),
        )
        rows = {row["source"]["path"]: row for row in result["actions"]}
        self.assertEqual(rows[self.invoice_a]["action"], "unreviewed")
        self.assertIn("digest", rows[self.invoice_a]["reason"])

    def test_legacy_clean_lines_do_not_establish_a_versioned_review(self):
        result = spend_action.build_action_list(
            self.case, self.root, self._manifest([], clean=["line-1"])
        )
        rows = {row["source"]["path"]: row for row in result["actions"]}
        self.assertEqual(rows[self.invoice_a]["action"], "unreviewed")

    def test_affected_line_must_exist(self):
        finding = self._finding()
        finding["affected_lines"][0]["line_id"] = "missing-line"
        with self.assertRaisesRegex(spend_action.SpendActionError, "affected line"):
            spend_action.build_action_list(self._case([finding]), self.root, self._manifest([]))

    def test_realized_without_credit_is_not_settled(self):
        finding = self._finding(status="realized")
        result = spend_action.build_action_list(
            self._case([finding]), self.root, self._manifest([])
        )
        row = self._row(result, self.invoice_a)
        self.assertEqual(row["action"], "review_required")
        self.assertEqual(row["settlement"]["status"], "resolution_claimed")

    def test_partial_credit_is_partially_settled(self):
        self._write_credit(4)
        finding = self._finding(
            status="realized", settlement_ref="credit-note:C-1", credit_path="credits.json"
        )
        result = spend_action.build_action_list(
            self._case([finding]), self.root, self._manifest([])
        )
        row = self._row(result, self.invoice_a)
        self.assertEqual(row["action"], "partially_settled")
        self.assertEqual(row["settlement"]["status"], "partially_settled")
        self.assertEqual(row["settlement"]["applied_amount"]["value"], "4.00")

    def test_unapplied_credit_is_not_settled(self):
        self._write_credit(10, applied=False)
        finding = self._finding(
            status="realized", settlement_ref="credit-note:C-1", credit_path="credits.json"
        )
        result = spend_action.build_action_list(
            self._case([finding]), self.root, self._manifest([])
        )
        row = self._row(result, self.invoice_a)
        self.assertEqual(row["action"], "review_required")
        self.assertEqual(row["settlement"]["status"], "credit_issued")

    def test_matching_applied_credit_is_verified_settlement(self):
        self._write_credit(10)
        finding = self._finding(
            status="realized", settlement_ref="credit-note:C-1", credit_path="credits.json"
        )
        result = spend_action.build_action_list(
            self._case([finding]), self.root, self._manifest([])
        )
        row = self._row(result, self.invoice_a)
        self.assertEqual(row["action"], "settled")
        self.assertEqual(row["settlement"]["status"], "settlement_verified")

    def test_kwd_amount_preserves_three_decimals(self):
        self._write_invoice("kwd.json", "kwd-line", 1.234, "KWD", "charge-kwd")
        case = self._case([], currency="KWD")
        review = self._review("kwd.json", "kwd-line")
        result = spend_action.build_action_list(
            case, self.root, self._manifest([review], invoices=["kwd.json"])
        )
        row = self._row(result, "kwd.json")
        self.assertEqual(row["amount"]["value"], "1.234")
        self.assertEqual(row["amount"]["currency"], "KWD")

    def test_case_level_amounts_stay_out_of_invoice_totals(self):
        line_finding = self._finding(finding_id="F-line")
        case_finding = self._finding(finding_id="F-case", affected=False)
        result = spend_action.build_action_list(
            self._case([line_finding, case_finding]), self.root, self._manifest([])
        )
        amounts = result["summary"]["amounts"][0]
        self.assertEqual(amounts["known_value"], "10.00")
        self.assertEqual(result["summary"]["case_level_amounts"][0]["known_value"], "10.00")

    def test_duplicate_finding_ids_are_refused(self):
        with self.assertRaisesRegex(spend_action.SpendActionError, "duplicate finding_id"):
            spend_action.build_action_list(
                self._case([self._finding(), self._finding()]),
                self.root,
                self._manifest([]),
            )


if __name__ == "__main__":
    unittest.main()
