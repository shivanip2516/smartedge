import json
from pathlib import Path
from unittest import TestCase


class TestSmartEdgeScrapRecoveryNaming(TestCase):
	@classmethod
	def setUpClass(cls):
		cls.doctype_path = (
			Path(__file__).resolve().parent
			/ "smart_edge_custom"
			/ "doctype"
			/ "smartedge_scrap_recovery"
			/ "smartedge_scrap_recovery.json"
		)
		cls.meta = json.loads(cls.doctype_path.read_text(encoding="utf-8"))

	def test_uses_braced_frappe_format_series(self):
		self.assertEqual(self.meta["autoname"], "format:REC-{#####}")

	def test_does_not_use_a_naming_series_field(self):
		fieldnames = {field["fieldname"] for field in self.meta["fields"]}
		self.assertNotIn("naming_series", fieldnames)

	def test_literal_malformed_pattern_is_not_configured(self):
		self.assertNotEqual(self.meta["autoname"], "format:REC-.#####")
