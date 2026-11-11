import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class TestPhase5LabelPrintingContract(unittest.TestCase):
	def test_print_format_targets_production_lot_and_qr(self):
		path = ROOT / "smart_edge_custom" / "print_format" / "smartedge_production_traceability_label" / "smartedge_production_traceability_label.json"
		template = path.with_suffix(".html")
		self.assertTrue(template.exists())
		template_text = template.read_text()
		self.assertIn("get_label_qr_code", template_text)
		self.assertIn("SE-LOT:", template_text)
		definition = json.loads(path.read_text())
		self.assertEqual(definition["doc_type"], "SmartEdge Production Lot")
		self.assertIn('"barcode_format":"QR"', definition["format_data"])

	def test_server_gate_requires_qc_and_stock_lot(self):
		source = (ROOT / "manufacturing" / "production_lot.py").read_text()
		for required in ("is_stock_inventory_batch", "custom_batch_purpose", "Physical Stock", "custom_smartedge_qc_status", "custom_accepted_qty", "docstatus", "get_print_label_url"):
			self.assertIn(required, source)


if __name__ == "__main__":
	unittest.main()
