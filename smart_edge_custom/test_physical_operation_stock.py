"""Safety tests for the opt-in physical operation stock path."""
import unittest
import logging
from types import SimpleNamespace
from unittest.mock import patch, Mock

import frappe

from smart_edge_custom.manufacturing import physical_operation_stock


class TestPhysicalOperationStockSafety(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "logger", return_value=logging.getLogger("physical-operation-test")))
		self.enterContext(patch.object(physical_operation_stock, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(physical_operation_stock, "nowdate", return_value="2026-11-03"))
		self.enterContext(patch("frappe.translate.get_all_translations", return_value={}))

	def test_feature_is_enabled_by_default(self):
		with patch.object(frappe, "conf", {}):
			self.assertTrue(physical_operation_stock.enabled())

	def test_feature_can_be_disabled_by_site_flag(self):
		with patch.object(frappe, "conf", {"smartedge_physical_operation_stock": 1}):
			self.assertTrue(physical_operation_stock.enabled())
		with patch.object(frappe, "conf", {"smartedge_physical_operation_stock": 0}):
			self.assertFalse(physical_operation_stock.enabled())

	def test_explicitly_disabled_path_never_posts(self):
		with patch.object(frappe, "conf", {"smartedge_physical_operation_stock": 0}), patch.object(physical_operation_stock, "_wip_warehouse") as warehouse:
			self.assertIsNone(physical_operation_stock.post_operation(None, None, None, None, lambda *_: "X"))
			warehouse.assert_not_called()

	def test_wip_warehouse_prefers_plan_entry(self):
		from smart_edge_custom.manufacturing import planning
		with patch.object(planning, "validate_stock_warehouse", return_value="Plan WIP") as validate:
			with patch.object(physical_operation_stock.frappe, "db", Mock()):
				self.assertEqual(physical_operation_stock._wip_warehouse(
					frappe._dict(wip_warehouse="Plan WIP", work_order="WO-1"), "Company"
				), "Plan WIP")
		validate.assert_called_once_with("Plan WIP", "Company", "Physical operation WIP Warehouse")

	def test_wip_warehouse_uses_work_order_then_company_default(self):
		from smart_edge_custom.manufacturing import planning
		db = Mock()
		db.get_value.side_effect = ["WO WIP", "Company WIP"]
		with patch.object(physical_operation_stock.frappe, "db", db), \
			patch.object(planning, "validate_stock_warehouse", side_effect=lambda value, company, label: value):
			self.assertEqual(physical_operation_stock._wip_warehouse(
				frappe._dict(name="PLAN-1", work_order="WO-1"), "Company"
			), "WO WIP")

	def test_wip_warehouse_missing_is_actionable(self):
		from smart_edge_custom.manufacturing import planning
		db = Mock()
		db.get_value.return_value = None
		with patch.object(physical_operation_stock.frappe, "db", db), \
			patch.object(planning, "validate_stock_warehouse"), \
			patch.object(physical_operation_stock.frappe, "throw", side_effect=frappe.ValidationError("wip required")):
			with self.assertRaisesRegex(frappe.ValidationError, "wip required"):
				physical_operation_stock._wip_warehouse(frappe._dict(name="PLAN-1"), "Company")

	def test_output_conversion_uses_existing_item_conversion(self):
		from smart_edge_custom.manufacturing import planning
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")), \
			patch.object(planning, "get_required_conversion_factor", return_value=4.356) as convert:
			row = physical_operation_stock.stock_row("FG", 39.784206, "WIP", inward=True)
		self.assertEqual(row["qty"], 39.784206)
		self.assertEqual(row["conversion_factor"], 4.356)
		self.assertEqual(row["stock_uom"], "Kg")
		self.assertEqual(row["t_warehouse"], "WIP")
		convert.assert_called_once_with("FG", "Meter", "Kg")

	def test_posting_quantity_uses_effective_erpnext_precision(self):
		def precision(doctype, fieldname):
			return {("Stock Entry Detail", "qty"): 6, ("Stock Entry Detail", "transfer_qty"): 3,
				("Serial and Batch Entry", "qty"): 3}.get((doctype, fieldname))
		with patch.object(physical_operation_stock.frappe, "get_precision", side_effect=precision):
			self.assertEqual(physical_operation_stock.effective_stock_precision(), 3)
			self.assertEqual(physical_operation_stock.normalize_posting_quantity(79.568411), 79.568)
			with patch.object(physical_operation_stock.frappe, "throw", side_effect=frappe.ValidationError("becomes zero")):
				with self.assertRaisesRegex(frappe.ValidationError, "becomes zero"):
					physical_operation_stock.normalize_posting_quantity(0.0004)

	def test_final_posting_consumes_wip_and_uses_stock_units_in_bundle(self):
		generator = frappe._dict(finished_item="FG", company="Company")
		job = frappe._dict(name="JOB", plan_entry="PLAN", input_production_lot="QC", stock_posting_qty=30)
		with patch.object(physical_operation_stock.frappe, "get_doc", return_value=frappe._dict()), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(physical_operation_stock, "physical_input_batch", return_value="QC-BATCH"), \
			patch.object(physical_operation_stock, "stock_row", return_value={"item_code": "FG", "qty": 30, "conversion_factor": 4.356}):
			rows, bundles = physical_operation_stock.final_wip_components(generator, [job])
		self.assertEqual([r["item_code"] for r in rows], ["FG"])
		self.assertAlmostEqual(bundles[0]["QC-BATCH"], 130.68)

	def test_precision_loss_is_rejected_before_submission(self):
		row = frappe._dict(qty=39.784206, conversion_factor=1, transfer_qty=39.784, uom="Meter", stock_uom="Meter")
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError("Stock precision")):
			with self.assertRaisesRegex(frappe.ValidationError, "Stock precision"):
				physical_operation_stock.validate_posted_precision(Mock(items=[row]))
		row.transfer_qty = 39.784206
		physical_operation_stock.validate_posted_precision(Mock(items=[row]))

	def test_submitted_retry_does_not_create_batch_or_stock_entry(self):
		from smart_edge_custom.manufacturing import planning
		job = frappe._dict(name="JOB", company="Company")
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=None), \
			patch.object(frappe, "db", Mock()), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(frappe, "get_all", return_value=[frappe._dict(name="STE", docstatus=1)]), \
			patch.object(physical_operation_stock, "_batch") as create_batch:
			self.assertEqual(physical_operation_stock.post_operation(frappe._dict(), job, frappe._dict(), None, Mock()), "STE")
			create_batch.assert_not_called()

	def test_scrap_requires_explicit_inventory_disposition(self):
		from smart_edge_custom.manufacturing import planning
		job = frappe._dict(name="JOB", rejected_qty=1)
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=None), \
			patch.object(frappe, "db", Mock()), \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError("disposition")):
			with self.assertRaisesRegex(frappe.ValidationError, "disposition"):
				physical_operation_stock.post_operation(frappe._dict(), job, frappe._dict(), None, Mock())

	def test_manufacture_and_partial_repack_submit_distinct_batches(self):
		"""Exercise posting orchestration with an in-memory transaction adapter.

		This validates row/bundle wiring and balances, not ERPNext valuation/SLE.
		"""
		from smart_edge_custom.manufacturing import planning, batch_genealogy
		balances = {"CMP": 173.0, "BASE": 300.0}
		entries = []
		class StockEntry:
			def __init__(self):
				self.items = []
				self.name = "STE-" + str(len(entries) + 1)
				entries.append(self)
			def append(self, field, values):
				self.items.append(frappe._dict(values))
			def set_stock_entry_type(self):
				self.stock_entry_type = self.purpose
			def insert(self, **kwargs):
				for row in self.items:
					row.transfer_qty = row.qty * row.conversion_factor
			def save(self, **kwargs):
				pass
			def submit(self):
				for row in self.items:
					change = row.transfer_qty * (-1 if row.get("s_warehouse") else 1)
					balances[row.batch_no] = balances.get(row.batch_no, 0) + change
					assert balances[row.batch_no] >= 0
				self.docstatus = 1
		def bundle(stock, row, batch, qty, warehouse, direction):
			self.assertEqual(qty, row.transfer_qty)
			row.batch_no = batch
		def component_bundles(stock, groups):
			for row, group in zip(stock.items, groups):
				if group:
					row.batch_no = next(iter(group))
		def save_batch(**kwargs):
			self.assertEqual(entries[-1].docstatus, 1)
		batch = SimpleNamespace(save=Mock(side_effect=save_batch))
		generator = frappe._dict(source_warehouse="Stores")
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", side_effect=[None, frappe._dict(name="EXT-ENTRY")]), \
			patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Meter")), \
			patch.object(planning, "get_required_conversion_factor", return_value=1), \
			patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), \
			patch.object(frappe, "get_all", return_value=[]), \
			patch.object(frappe, "new_doc", side_effect=lambda dt: StockEntry()), \
			patch.object(frappe, "get_doc", side_effect=lambda dt, name: generator if dt == "Final EB BOM Generator" else batch), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(physical_operation_stock, "_batch", side_effect=["EXT", "QC"]), \
			patch.object(physical_operation_stock, "physical_input_batch", return_value="EXT"), \
			patch.object(physical_operation_stock, "_bundle", side_effect=bundle), \
			patch.object(planning, "get_final_stock_components", return_value=[
				{"item_code": "COMPOUND", "qty": 173, "conversion_factor": 1, "s_warehouse": "Stores"},
				{"item_code": "BASE", "qty": 0.3, "conversion_factor": 1000, "s_warehouse": "Stores"}]) as materials, \
			patch.object(planning, "get_final_component_batch_nos", return_value=[{"CMP": 173}, {"BASE": 300}]), \
			patch.object(planning, "set_final_component_batch_bundles", side_effect=component_bundles), \
			patch.object(batch_genealogy, "capture_stock_entry"):
			job = frappe._dict(name="EXT-JOB", company="Company", good_qty=39.784206, actual_qty_meter=39.784206, final_eb_bom_generator="GEN")
			job.db_set = Mock()
			lot = frappe._dict(name="EXT-LOT", finished_item="FG", input_production_lot=None)
			physical_operation_stock.post_operation(lot, job, frappe._dict(), None, Mock())
			self.assertEqual(balances["EXT"], 39.784206)
			self.assertEqual(batch.custom_batch_purpose, "Physical Stock")
			self.assertEqual(entries[0].purpose, "Manufacture")
			self.assertEqual((balances["CMP"], balances["BASE"]), (0, 0))
			lot.input_production_lot = "EXT-LOT"
			job.name, job.good_qty = "QC-JOB", 30
			physical_operation_stock.post_operation(lot, job, frappe._dict(), None, Mock())
			self.assertAlmostEqual(balances["EXT"], 9.784206)
			self.assertEqual(balances["QC"], 30)
			self.assertEqual(entries[1].purpose, "Repack")
			self.assertEqual(materials.call_count, 1)

	def test_upstream_cancellation_requires_downstream_reversal(self):
		from smart_edge_custom.manufacturing import batch_genealogy
		doc = frappe._dict(name="STE-EXT", remarks="SmartEdge physical operation JOB-EXT")
		with patch.object(batch_genealogy, "extract_batch_movements", return_value=[{"direction": "Output", "batch_no": "EXT"}]), \
			patch.object(frappe, "get_all", return_value=["STE-QC"]), \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError("Cancel downstream")):
			with self.assertRaisesRegex(frappe.ValidationError, "Cancel downstream"):
				physical_operation_stock.prevent_upstream_cancellation(doc)
		with patch.object(batch_genealogy, "extract_batch_movements", return_value=[{"direction": "Output", "batch_no": "EXT"}]), \
			patch.object(frappe, "get_all", return_value=[]):
			physical_operation_stock.prevent_upstream_cancellation(doc)
