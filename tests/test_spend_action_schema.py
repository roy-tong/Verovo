import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "healthcheck"))

from verovo_healthcheck import spend_action  # noqa: E402


class SpendActionSchemaTests(unittest.TestCase):
    def test_schema_matches_public_action_vocabulary(self):
        schema = json.loads((ROOT / "schemas" / "spend-action-list.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        action = schema["$defs"]["action"]
        self.assertEqual(tuple(action["enum"]), spend_action.ACTION_ORDER)
        for field in (
            "schema", "case_id", "buyer", "vendor", "period", "actions",
            "summary", "executes_payment", "buyer_decision_required",
        ):
            self.assertIn(field, schema["required"])
        self.assertFalse(schema["properties"]["executes_payment"]["const"])

    def test_golden_output_has_the_declared_contract(self):
        case = json.loads((ROOT / "examples" / "audit-golden-case" / "audit-case.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "examples" / "audit-golden-case" / "manifest.json").read_text(encoding="utf-8"))
        result = spend_action.build_action_list(
            case, ROOT / "examples" / "audit-golden-case", manifest
        )
        schema = json.loads((ROOT / "schemas" / "spend-action-list.schema.json").read_text(encoding="utf-8"))
        for field in schema["required"]:
            self.assertIn(field, result)
        for row in result["actions"]:
            self.assertIn(row["action"], spend_action.ACTION_ORDER)
            self.assertIsInstance(row["finding_ids"], list)
            if row["amount"] is not None:
                self.assertRegex(row["amount"]["value"], r"^\d+\.\d{2}$")
        settled = next(row for row in result["actions"] if row["source"]["kind"] == "invoice_line" and row["source"]["line_id"] == "line-003")
        self.assertEqual(settled["settlement"]["status"], "settlement_verified")
        ready = next(row for row in result["actions"] if row["source"]["kind"] == "invoice_line" and row["source"]["line_id"] == "line-004")
        self.assertIn("source_digest", ready["charge_review"])

    def test_schema_supports_currency_specific_minor_units(self):
        import re
        schema = json.loads((ROOT / "schemas" / "spend-action-list.schema.json").read_text(encoding="utf-8"))
        pattern = schema["$defs"]["amount"]["properties"]["value"]["pattern"]
        self.assertIsNotNone(re.fullmatch(pattern, "1.234"))
        self.assertIsNotNone(re.fullmatch(pattern, "1.23"))
        self.assertIsNone(re.fullmatch(pattern, "1.2"))
        self.assertIn("partially_settled", schema["$defs"]["settlement"]["properties"]["status"]["enum"])
        self.assertIn("case_level_amounts", schema["properties"]["summary"]["required"])


if __name__ == "__main__":
    unittest.main()
