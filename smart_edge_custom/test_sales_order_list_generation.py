import unittest
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.manufacturing import sales_order_list_generation as integration


class TestSalesOrderListGeneration(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(integration, "_", side_effect=lambda text: text))
		frappe.local.db = Mock()
		self.addCleanup(delattr, frappe.local, "db")

	def test_pending_rows_exclude_fulfilled_lines(self):
		order = frappe._dict(name="SO-1", customer="Customer", docstatus=1, status="To Deliver")
		order.check_permission = lambda permission: None
		overview = {"items": [
			{"sales_order_item": "ROW-1", "item_code": "IT-1", "item_name": "FG 1", "stock_uom": "Meter", "outstanding_qty": 100, "manufacturing_shortage": 100, "idx": 1},
			{"sales_order_item": "ROW-2", "item_code": "IT-2", "item_name": "FG 2", "stock_uom": "Meter", "outstanding_qty": 0, "manufacturing_shortage": 0},
		]}
		with patch.object(integration.frappe, "has_permission"), patch.object(integration.frappe.db, "get_value", return_value="ABC Industries"), patch.object(integration, "get_valid_sales_order", return_value=order), patch.object(integration, "build_manufacturing_overview", return_value=overview), patch.object(integration, "allocate_finished_stock"):
			rows = integration.get_pending_fg_requirements(["SO-1"])
		self.assertEqual([row["sales_order_item"] for row in rows], ["ROW-1"])

	def test_generation_revalidates_and_uses_existing_generator_service(self):
		order = frappe._dict(name="SO-1")
		overview = {"items": [{"sales_order_item": "ROW-1", "manufacturing_shortage": 250}]}
		created = {"name": "FEBG-1", "quantity": 250}
		with patch.object(integration.frappe, "has_permission"), patch.object(integration, "get_valid_sales_order", return_value=order), patch.object(integration, "build_manufacturing_overview", return_value=overview), patch.object(integration, "create_final_eb_bom_generator", return_value=created) as create:
			result = integration.generate_fg_and_bom([{ "sales_order": "SO-1", "sales_order_item": "ROW-1", "pending_qty": 1 }])
		create.assert_called_once_with("SO-1", "ROW-1", 250.0)
		self.assertEqual(result[0]["name"], "FEBG-1")

	def test_required_production_uses_allocator_shortage(self):
		orders = [frappe._dict(name="SO-1", customer="C-1", priority="High"), frappe._dict(name="SO-2", customer="C-2", priority="Low")]
		rows = [
			{"sales_order_item": "ROW-1", "item_code": "IT-1", "item_name": "FG", "stock_uom": "Meter", "outstanding_qty": 100, "manufacturing_shortage": 100, "idx": 1},
			{"sales_order_item": "ROW-2", "item_code": "IT-1", "item_name": "FG", "stock_uom": "Meter", "outstanding_qty": 100, "manufacturing_shortage": 100, "idx": 1},
		]
		def allocate(values):
			values[0]["manufacturing_shortage"] = 0
			values[1]["manufacturing_shortage"] = 100
		with patch.object(integration.frappe, "has_permission"), patch.object(integration.frappe.db, "get_value", return_value="Customer"), patch.object(integration, "get_valid_sales_order", side_effect=orders), patch.object(integration, "build_manufacturing_overview", side_effect=[{"items": [rows[0]]}, {"items": [rows[1]]}]), patch.object(integration, "allocate_finished_stock", side_effect=allocate):
			result = integration.get_pending_fg_requirements(["SO-1", "SO-2"])
		self.assertEqual([(row["sales_order"], row["pending_qty"]) for row in result], [("SO-2", 100)])

	def test_invalid_selection_is_rejected(self):
		with patch.object(integration.frappe, "has_permission"), patch.object(integration.frappe, "throw", side_effect=lambda message: (_ for _ in ()).throw(frappe.ValidationError(message))):
			with self.assertRaises(frappe.ValidationError):
				integration.generate_fg_and_bom([])


if __name__ == "__main__":
	unittest.main()
