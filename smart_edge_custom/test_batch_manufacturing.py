from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.batch_manufacturing import (
	_make_finished_bundle,
	allocate_finished_batches,
	calculate_manufacturing_quantity,
	get_complete_batch_count,
	make_batch_work_order,
	set_work_order_batch_quantity,
	validate_batch_stock_entry_quantity,
	validate_bom,
)


class TestBatchManufacturing(IntegrationTestCase):
	def test_five_gram_based_batches_calculate_stock_uom_quantity(self):
		self.assertEqual(calculate_manufacturing_quantity(5, 0.3), 1.5)

	def test_four_batches_calculate_260_kg(self):
		self.assertEqual(calculate_manufacturing_quantity(4, 65), 260)
		for batches, weight in ((0, 65), (-1, 65), (1.5, 65), (1, 0)):
			with self.assertRaises(frappe.ValidationError):
				calculate_manufacturing_quantity(batches, weight)

	def test_partial_completion_uses_complete_batches(self):
		self.assertEqual(get_complete_batch_count(130, 65), 2)
		with self.assertRaises(frappe.ValidationError):
			get_complete_batch_count(100, 65)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	def test_single_batch_bom_uses_compound_total(self, get_details):
		get_details.return_value = {
			"is_batch_manufacturing_item": True,
			"output_qty": 65,
			"stock_uom": "Kg",
		}
		doc = MagicMock()
		doc.item = "Compound 1"
		doc.get.return_value = 1
		doc.is_new.return_value = True

		validate_bom(doc)

		self.assertEqual(doc.quantity, 65)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	def test_single_batch_bom_uses_base_colour_total(self, get_details):
		get_details.return_value = {
			"is_batch_manufacturing_item": True,
			"output_qty": 50,
			"stock_uom": "Kg",
		}
		doc = MagicMock()
		doc.item = "Base Colour 1"
		doc.get.return_value = 1
		doc.is_new.return_value = True

		validate_bom(doc)

		self.assertEqual(doc.quantity, 50)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details")
	def test_work_order_uses_number_of_batches(self, get_details, get_item_details):
		get_item_details.return_value = {"is_batch_manufacturing_item": True}
		get_details.return_value = {"applicable": True, "weight_per_batch": 65}
		doc = MagicMock()
		doc.production_item = "Compound 1"
		doc.bom_no = "BOM-TEST"
		doc.docstatus = 0
		doc.qty = 260
		doc.get.return_value = 4
		doc.is_new.return_value = True
		doc.precision.return_value = 6

		set_work_order_batch_quantity(doc)

		self.assertEqual(doc.custom_no_of_batches, 4)
		self.assertEqual(doc.custom_weight_per_batch, 65)
		self.assertEqual(doc.qty, 260)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	def test_non_batch_work_order_is_unchanged(self, get_item_details):
		get_item_details.return_value = {"is_batch_manufacturing_item": False}
		doc = MagicMock(qty=7)
		doc.production_item = "STANDARD-ITEM"
		doc.bom_no = "BOM-STANDARD"
		doc.docstatus = 0

		set_work_order_batch_quantity(doc)

		self.assertEqual(doc.qty, 7)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details")
	def test_work_order_rejects_incorrect_calculated_quantity(self, get_details, get_item_details):
		get_item_details.return_value = {"is_batch_manufacturing_item": True}
		get_details.return_value = {"applicable": True, "weight_per_batch": 65}
		doc = MagicMock()
		doc.production_item = "Compound 1"
		doc.bom_no = "BOM-TEST"
		doc.docstatus = 0
		doc.qty = 100
		doc.get.return_value = 4
		doc.is_new.return_value = True
		doc.precision.return_value = 6

		with self.assertRaisesRegex(frappe.ValidationError, "calculated from 4 batches"):
			set_work_order_batch_quantity(doc)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details")
	def test_work_order_rejects_mismatched_bom(self, get_details, get_item_details):
		get_item_details.return_value = {"is_batch_manufacturing_item": True}
		get_details.return_value = {"applicable": False}
		doc = MagicMock()
		doc.production_item = "Compound 1"
		doc.bom_no = "BOM-OTHER-ITEM"
		doc.docstatus = 0
		doc.is_new.return_value = True

		with self.assertRaisesRegex(frappe.ValidationError, "one-batch BOM for Item"):
			set_work_order_batch_quantity(doc)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	def test_submitted_work_order_is_not_recalculated(self, get_item_details):
		doc = MagicMock()
		doc.docstatus = 1
		doc.qty = 220

		set_work_order_batch_quantity(doc)

		self.assertEqual(doc.qty, 220)
		get_item_details.assert_not_called()

	@patch("erpnext.manufacturing.doctype.work_order.work_order.make_work_order")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details")
	def test_bom_popup_creates_work_order_with_batch_fields(self, get_details, core_make_work_order):
		get_details.return_value = {
			"applicable": True,
			"weight_per_batch": 55,
			"stock_uom": "Kg",
		}
		work_order = frappe._dict()
		core_make_work_order.return_value = work_order

		result = make_batch_work_order("BOM-Compound 3-001", "Compound 3", 4, "Smart Edge")

		core_make_work_order.assert_called_once_with(
			bom_no="BOM-Compound 3-001",
			item="Compound 3",
			qty=220,
			company="Smart Edge",
			project=None,
			use_multi_level_bom=None,
		)
		self.assertEqual(result.custom_no_of_batches, 4)
		self.assertEqual(result.custom_weight_per_batch, 55)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.get_value")
	def test_material_transfer_requires_complete_batches(self, get_value, get_item_details):
		get_value.return_value = frappe._dict(
			{
				"production_item": "Compound 3",
				"custom_weight_per_batch": 55,
				"docstatus": 1,
			}
		)
		get_item_details.return_value = {"is_batch_manufacturing_item": True}
		doc = frappe._dict(
			{
				"purpose": "Material Transfer for Manufacture",
				"work_order": "WO-TEST",
				"fg_completed_qty": 100,
			}
		)

		with self.assertRaisesRegex(frappe.ValidationError, "complete batches of 55"):
			validate_batch_stock_entry_quantity(doc)

		doc.fg_completed_qty = 110
		validate_batch_stock_entry_quantity(doc)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing._make_finished_bundle")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.sql")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.get_value")
	def test_partial_stock_entry_creates_two_batches(
		self, get_value, _sql, get_details, make_finished_bundle
	):
		get_value.return_value = frappe._dict(
			{
				"production_item": "Compound 1",
				"bom_no": "BOM-TEST",
				"custom_no_of_batches": 4,
				"custom_weight_per_batch": 65,
				"docstatus": 1,
			}
		)
		get_details.return_value = {"is_batch_manufacturing_item": True}
		row = frappe._dict(
			{
				"idx": 1,
				"is_finished_item": 1,
				"item_code": "Compound 1",
				"transfer_qty": 130,
				"serial_and_batch_bundle": None,
			}
		)
		doc = MagicMock()
		doc.purpose = "Manufacture"
		doc.work_order = "WO-TEST"
		doc.name = "STE-TEST"
		doc.get.return_value = [row]

		allocate_finished_batches(doc)

		make_finished_bundle.assert_called_once_with(doc, row, 2, 65.0)

	@patch("smart_edge_custom.manufacturing.batch_manufacturing._make_finished_bundle")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.get_item_batch_details")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.sql")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.get_value")
	def test_incomplete_batch_blocks_before_creation(
		self, get_value, _sql, get_details, make_finished_bundle
	):
		get_value.return_value = frappe._dict(
			{
				"production_item": "Compound 1",
				"bom_no": "BOM-TEST",
				"custom_weight_per_batch": 65,
				"docstatus": 1,
			}
		)
		get_details.return_value = {"is_batch_manufacturing_item": True}
		row = frappe._dict(
			{
				"idx": 1,
				"is_finished_item": 1,
				"item_code": "Compound 1",
				"transfer_qty": 100,
				"serial_and_batch_bundle": None,
			}
		)
		doc = MagicMock()
		doc.purpose = "Manufacture"
		doc.work_order = "WO-TEST"
		doc.name = "STE-TEST"
		doc.get.return_value = [row]

		with self.assertRaises(frappe.ValidationError):
			allocate_finished_batches(doc)

		make_finished_bundle.assert_not_called()

	@patch("erpnext.stock.serial_batch_bundle.SerialBatchCreation")
	@patch("erpnext.stock.doctype.batch.batch.make_batch")
	@patch("smart_edge_custom.manufacturing.batch_manufacturing.frappe.db.get_value")
	def test_finished_bundle_has_one_unique_inventory_batch_per_batch(
		self, get_value, make_batch, serial_batch_creation
	):
		get_value.return_value = frappe._dict(
			{"create_new_batch": 1, "batch_number_series": "TEST-.#####", "has_serial_no": 0}
		)
		make_batch.side_effect = ["BATCH-1", "BATCH-2", "BATCH-3", "BATCH-4"]
		serial_batch_creation.return_value.make_serial_and_batch_bundle.return_value = frappe._dict(
			{"name": "SABB-TEST"}
		)
		doc = frappe._dict(
			{
				"doctype": "Stock Entry",
				"name": "STE-TEST",
				"posting_date": "2026-10-19",
				"posting_time": "12:00:00",
				"company": "Test Company",
			}
		)
		row = frappe._dict(
			{
				"name": "STE-DETAIL-1",
				"item_code": "Compound 1",
				"t_warehouse": "Finished Goods - TC",
				"transfer_qty": 260,
			}
		)

		_make_finished_bundle(doc, row, 4, 65)

		self.assertEqual(make_batch.call_count, 4)
		serial_batch_creation.return_value.make_serial_and_batch_bundle.assert_called_once_with(
			batch_nos={"BATCH-1": 65, "BATCH-2": 65, "BATCH-3": 65, "BATCH-4": 65}
		)
		self.assertEqual(row.serial_and_batch_bundle, "SABB-TEST")
