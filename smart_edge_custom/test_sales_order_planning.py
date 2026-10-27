from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from smart_edge_custom.manufacturing.final_bom_generator import validate_ready_for_planning
from smart_edge_custom.manufacturing.sales_order_planning import (
	allocate_finished_stock,
	create_final_eb_bom_generator,
	get_existing_coverage,
)
from smart_edge_custom.smart_edge_custom.doctype.final_eb_bom_generator.final_eb_bom_generator import (
	FinalEBBOMGenerator,
)


class TestSalesOrderManufacturingPlanning(TestCase):
	def make_requirement(self, outstanding, coverage=0, idx=1):
		return {
			"sales_order_item": f"SO-ITEM-{idx}",
			"idx": idx,
			"item_code": "SE-101",
			"warehouse": "Finished Goods - SE",
			"outstanding_stock_qty": outstanding,
			"existing_planned_qty": coverage,
			"own_reserved_qty": 0,
			"reserve_stock": False,
		}

	@patch("smart_edge_custom.manufacturing.sales_order_planning.get_bin_quantities")
	def test_full_stock_has_no_shortage(self, get_bin):
		get_bin.return_value = frappe._dict({"actual_qty": 1000, "reserved_qty": 1000, "reserved_stock": 0})
		rows = [self.make_requirement(1000)]

		allocate_finished_stock(rows)

		self.assertEqual(rows[0]["available_fg_stock"], 1000)
		self.assertEqual(rows[0]["manufacturing_shortage"], 0)

	@patch("smart_edge_custom.manufacturing.sales_order_planning.get_bin_quantities")
	def test_no_stock_requires_full_outstanding_quantity(self, get_bin):
		get_bin.return_value = frappe._dict({"actual_qty": 0, "reserved_qty": 1000, "reserved_stock": 0})
		rows = [self.make_requirement(1000)]

		allocate_finished_stock(rows)

		self.assertEqual(rows[0]["available_fg_stock"], 0)
		self.assertEqual(rows[0]["manufacturing_shortage"], 1000)

	@patch("smart_edge_custom.manufacturing.sales_order_planning.get_bin_quantities")
	def test_partial_stock_calculates_remaining_shortage(self, get_bin):
		get_bin.return_value = frappe._dict({"actual_qty": 1500, "reserved_qty": 5000, "reserved_stock": 0})
		rows = [self.make_requirement(5000)]

		allocate_finished_stock(rows)

		self.assertEqual(rows[0]["available_fg_stock"], 1500)
		self.assertEqual(rows[0]["manufacturing_shortage"], 3500)

	@patch("smart_edge_custom.manufacturing.sales_order_planning.get_bin_quantities")
	def test_existing_coverage_reduces_requirement_without_double_counting_stock(self, get_bin):
		get_bin.return_value = frappe._dict({"actual_qty": 0, "reserved_qty": 3500, "reserved_stock": 0})
		rows = [self.make_requirement(3500, coverage=2000)]

		allocate_finished_stock(rows)

		self.assertEqual(rows[0]["manufacturing_shortage"], 1500)

	@patch("smart_edge_custom.manufacturing.sales_order_planning.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.sales_order_planning.frappe.get_meta")
	def test_completed_and_cancelled_generators_are_not_future_coverage(self, get_meta, get_all):
		get_meta.return_value.has_field.return_value = True
		get_all.return_value = [
			frappe._dict({"name": "FEBG-A", "sales_order_item": "SO-ITEM-1", "production_quantity": 2000, "status": "Planned"}),
			frappe._dict({"name": "FEBG-B", "sales_order_item": "SO-ITEM-1", "production_quantity": 1000, "status": "Completed"}),
			frappe._dict({"name": "FEBG-C", "sales_order_item": "SO-ITEM-1", "production_quantity": 500, "status": "Cancelled"}),
		]

		coverage, linked = get_existing_coverage("SO-1")

		self.assertEqual(coverage["SO-ITEM-1"], 2000)
		self.assertEqual(len(linked), 3)

	@patch("smart_edge_custom.manufacturing.sales_order_planning.build_manufacturing_overview")
	@patch("smart_edge_custom.manufacturing.sales_order_planning.get_valid_sales_order")
	@patch("smart_edge_custom.manufacturing.sales_order_planning.frappe.new_doc")
	@patch("smart_edge_custom.manufacturing.sales_order_planning.lock_sales_order")
	@patch("smart_edge_custom.manufacturing.sales_order_planning.frappe.has_permission")
	def test_creation_rechecks_shortage_and_links_generator(
		self, has_permission, lock_sales_order, new_doc, get_order, get_overview
	):
		has_permission.return_value = True
		order = frappe._dict({"name": "SO-1", "company": "SmartEdge", "customer": "Customer A", "delivery_date": "2026-11-01"})
		get_order.return_value = order
		get_overview.return_value = {
			"items": [
				{
					"sales_order_item": "SO-ITEM-1",
					"item_code": "SE-101",
					"manufacturing_shortage": 1500,
					"stock_uom": "Meter",
					"delivery_date": "2026-10-31",
				}
			]
		}
		generator = MagicMock()
		generator.name = "FEBG-1"
		generator.production_quantity = 1500
		generator.stock_uom = "Meter"
		new_doc.return_value = generator

		result = create_final_eb_bom_generator("SO-1", "SO-ITEM-1", 1500)

		lock_sales_order.assert_called_once_with("SO-1")
		generator.insert.assert_called_once_with()
		self.assertEqual(generator.sales_order, "SO-1")
		self.assertEqual(generator.sales_order_item, "SO-ITEM-1")
		self.assertEqual(generator.finished_item, "SE-101")
		self.assertNotIn("compound", generator.__dict__)
		self.assertNotIn("base_colour", generator.__dict__)
		self.assertEqual(result["remaining_shortage"], 0)

	def test_sales_order_linked_generator_can_start_without_materials(self):
		doc = frappe._dict({"sales_order": "SO-1", "compound": None, "base_colour": None})

		FinalEBBOMGenerator.validate_initial_material_configuration(doc)

	def test_manual_generator_still_requires_materials_on_save(self):
		doc = frappe._dict({"sales_order": None, "compound": None, "base_colour": None})

		with self.assertRaisesRegex(frappe.ValidationError, "Compound is required"):
			FinalEBBOMGenerator.validate_initial_material_configuration(doc)

	@patch("smart_edge_custom.manufacturing.final_bom_generator.apply_preview_to_doc")
	@patch("smart_edge_custom.manufacturing.final_bom_generator.calculate_bom_preview")
	def test_planning_still_requires_material_configuration(self, calculate_preview, apply_preview):
		calculate_preview.return_value = {}
		doc = MagicMock()
		values = {
			"finished_item": "SE-101",
			"company": "SmartEdge",
			"compound": None,
			"base_colour": None,
			"extrusion_type": "Single Mould Extrusion",
		}
		doc.get.side_effect = values.get

		with self.assertRaisesRegex(frappe.ValidationError, "Compound is required"):
			validate_ready_for_planning(doc)
