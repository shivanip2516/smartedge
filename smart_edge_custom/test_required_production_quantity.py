import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing.sales_order_planning import create_final_eb_bom_generator_from_sources
from smart_edge_custom.manufacturing.sales_order_planning_status import add_legacy_quantity


class TestRequiredProductionQuantity(TestCase):
	def setUp(self):
		frappe.local.db = Mock()
		self.addCleanup(delattr, frappe.local, "db")

	def test_sales_order_sources_populate_required_quantity_only(self):
		generator = frappe._dict(name="FEBG-1", stock_uom="Meter")
		generator.append = Mock()
		generator.insert = Mock()
		meta = Mock()
		meta.has_field.return_value = True
		sources = [
			{
				"sales_order": "SO-1", "sales_order_item": "SO-ITEM-1", "customer": "Customer A",
				"company": "SmartEdge", "finished_item": "FG-1", "source_quantity": 350,
				"source_uom": "Meter", "converted_production_quantity": 350,
				"production_uom": "Meter",
			},
			{
				"sales_order": "SO-2", "sales_order_item": "SO-ITEM-2", "customer": "Customer B",
				"company": "SmartEdge", "finished_item": "FG-1", "source_quantity": 200,
				"source_uom": "Meter", "converted_production_quantity": 200,
				"production_uom": "Meter",
			},
		]
		with patch.object(frappe, "get_meta", return_value=meta), patch.object(
			frappe.local.db, "exists", return_value=True
		), patch.object(frappe, "new_doc", return_value=generator):
			result = create_final_eb_bom_generator_from_sources(sources)

		self.assertIs(result, generator)
		self.assertEqual(generator.required_production_quantity, 550)
		self.assertNotIn("production_quantity", generator)
		self.assertEqual(generator.append.call_count, 2)
		generator.insert.assert_called_once_with()

	def test_required_quantity_field_is_read_only_and_adjacent_to_production_quantity(self):
		path = (
			Path(__file__).resolve().parent
			/ "smart_edge_custom"
			/ "doctype"
			/ "final_eb_bom_generator"
			/ "final_eb_bom_generator.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in meta["fields"]}
		order = meta["field_order"]

		self.assertEqual(fields["required_production_quantity"]["fieldtype"], "Float")
		self.assertEqual(fields["required_production_quantity"]["label"], "Required Production Quantity")
		self.assertEqual(fields["required_production_quantity"]["read_only"], 1)
		self.assertEqual(order[order.index("required_production_quantity") - 1], "production_quantity")

	def test_legacy_planning_coverage_prefers_required_quantity_with_production_fallback(self):
		planned = {}
		generator = frappe._dict(
			sales_order="SO-1", sales_order_item="SO-ITEM-1", finished_item="FG-1",
			required_production_quantity=550, production_quantity=0,
		)
		items = {"SO-ITEM-1": {"item_code": "FG-1"}}

		add_legacy_quantity(planned, generator, "SO-1", items)

		self.assertEqual(planned, {"SO-ITEM-1": 550})


if __name__ == "__main__":
	import unittest

	unittest.main()
