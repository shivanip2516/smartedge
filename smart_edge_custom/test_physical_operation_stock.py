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

	def test_feature_is_disabled_by_default(self):
		with patch.object(frappe, "conf", {}):
			self.assertFalse(physical_operation_stock.enabled())

	def test_feature_requires_explicit_site_flag(self):
		with patch.object(frappe, "conf", {"smartedge_physical_operation_stock": 1}):
			self.assertTrue(physical_operation_stock.enabled())

	def test_packing_receipt_reference_is_unique_per_roll(self):
		self.assertEqual(
			physical_operation_stock.packing_stock_entry_reference("PACK-1", "SE-ROLL-00029"),
			"SmartEdge physical Packing roll SE-ROLL-00029",
		)
		self.assertNotEqual(
			physical_operation_stock.packing_stock_entry_reference("PACK-1", "SE-ROLL-00029"),
			physical_operation_stock.packing_stock_entry_reference("PACK-1", "SE-ROLL-00030"),
		)

	def test_disabled_path_never_posts(self):
		with patch.object(frappe, "conf", {}), patch.object(physical_operation_stock, "_wip_warehouse") as warehouse:
			self.assertIsNone(physical_operation_stock.post_operation(None, None, None, None, lambda *_: "X"))
			warehouse.assert_not_called()

	def test_only_explicit_non_final_route_stage_posts(self):
		from smart_edge_custom.manufacturing import planning
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(planning, "is_final_stock_posting_entry", return_value=False):
			self.assertFalse(physical_operation_stock.should_post_operation(frappe._dict()))
			self.assertFalse(physical_operation_stock.should_post_operation(frappe._dict(stock_movement_type="Non-Stock Operation")))
			self.assertTrue(physical_operation_stock.should_post_operation(frappe._dict(stock_movement_type="Stockable WIP Output")))
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(planning, "is_final_stock_posting_entry", return_value=True):
			self.assertFalse(physical_operation_stock.should_post_operation(frappe._dict(stock_movement_type="Stockable WIP Output")))

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

	def test_operation_rolls_follow_upstream_plan_chain(self):
		db = Mock()
		db.get_value.side_effect = [
			frappe._dict(name="PLAN-QC", released_from_plan_entry="PLAN-EXT"),
			frappe._dict(name="PLAN-EXT", released_from_plan_entry=None),
		]
		rolls = [frappe._dict(name="SE-ROLL-1", current_meter_quantity=1000)]
		with patch.object(physical_operation_stock.frappe, "db", db), patch.object(
			physical_operation_stock.frappe, "get_all", return_value=rolls
		) as get_all:
			result = physical_operation_stock._eligible_operation_rolls(
				frappe._dict(released_from_plan_entry="PLAN-QC")
			)
		self.assertEqual(result, rolls)
		self.assertEqual(get_all.call_args.kwargs["filters"]["source_plan_entry"], ["in", ["PLAN-QC", "PLAN-EXT"]])

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
			patch.object(physical_operation_stock, "validate_physical_batch_for_production"), \
			patch.object(physical_operation_stock, "_batch_source", return_value="WIP"), \
			patch.object(physical_operation_stock, "stock_row", return_value={"item_code": "FG", "qty": 30, "conversion_factor": 4.356}):
			rows, bundles = physical_operation_stock.final_wip_components(generator, [job])
		self.assertEqual([r["item_code"] for r in rows], ["FG"])
		self.assertAlmostEqual(bundles[0]["QC-BATCH"], 130.68)

	def test_final_posting_validates_aggregate_batch_availability(self):
		generator = frappe._dict(finished_item="FG", company="Company")
		jobs = [frappe._dict(name=f"JOB-{index}", plan_entry="PLAN", input_production_lot="QC", stock_posting_qty=30)
			for index in range(2)]
		with patch.object(physical_operation_stock.frappe, "get_doc", return_value=frappe._dict()), \
			patch.object(physical_operation_stock, "physical_input_batch", return_value="QC-BATCH"), \
			patch.object(physical_operation_stock, "validate_physical_batch_for_production"), \
			patch.object(physical_operation_stock, "stock_row", return_value={"item_code": "FG", "qty": 30, "conversion_factor": 2}), \
			patch.object(physical_operation_stock, "_batch_source", return_value="WIP") as source:
			rows, bundles = physical_operation_stock.final_wip_components(generator, jobs)
			self.assertEqual(len(rows), 2)
			self.assertEqual(bundles, [{"QC-BATCH": 60}, {"QC-BATCH": 60}])
			source.assert_called_once_with("QC-BATCH", "FG", 120, "Company")

	def test_physical_input_skips_nonstock_qc_lot(self):
		with patch.object(physical_operation_stock, "_physical_batch_for_lot", side_effect=[None, "EXT-BATCH"]), \
			patch.object(frappe, "db", Mock(get_value=Mock(return_value="EXT-LOT"))):
			self.assertEqual(physical_operation_stock.find_physical_input_lot("QC-LOT"), "EXT-LOT")

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
		with patch.object(physical_operation_stock, "should_post_operation", return_value=True), \
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
		with patch.object(physical_operation_stock, "should_post_operation", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=None), \
			patch.object(frappe, "db", Mock()), \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError("disposition")):
			with self.assertRaisesRegex(frappe.ValidationError, "disposition"):
				physical_operation_stock.post_operation(frappe._dict(), job, frappe._dict(), None, Mock())

	def test_authorized_roll_rework_is_not_blocked_by_parent_disposition_fields(self):
		"""A validated Roll rework posts only its Roll quantity; parent guards stay for normal jobs."""
		from smart_edge_custom.manufacturing import planning
		meta = Mock()
		meta.has_field.side_effect = lambda field: field in {"rework_roll", "rework_stock_entry"}
		job = frappe._dict(
			name="REWORK-JOB-800", company="Company", rework_roll="SE-ROLL-00057",
			rejected_qty=800, rework_qty=800, actual_qty_meter=3236, meta=meta,
		)
		with patch.object(physical_operation_stock, "should_post_operation", return_value=True), \
			patch.object(physical_operation_stock, "_validated_rework_roll_quantity", return_value=800), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=None), \
			patch.object(frappe, "db", Mock()), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(frappe, "get_all", return_value=[frappe._dict(name="STE-REWORK-800", docstatus=1)]), \
			patch.object(physical_operation_stock, "link_operation_posting") as link:
			self.assertEqual(
				physical_operation_stock.post_operation(frappe._dict(), job, frappe._dict(), None, Mock()),
				"STE-REWORK-800",
			)
		link.assert_called_once()

	def test_partial_qc_handoff_moves_only_accepted_quantity(self):
		entry = frappe._dict(planning_category="QC", operation="QC After Extrusion")
		job = frappe._dict(actual_qty_meter=3000, good_qty=2236)
		self.assertEqual(physical_operation_stock._physical_handoff_quantity(entry, job), 2236)

	def test_non_qc_handoff_retains_actual_quantity_rule(self):
		entry = frappe._dict(planning_category="Printing", operation="Printing")
		job = frappe._dict(actual_qty_meter=3000, good_qty=1800)
		self.assertEqual(physical_operation_stock._physical_handoff_quantity(entry, job), 3000)

	def test_legacy_rollwise_first_operation_wrapper_uses_aggregate_posting(self):
		with patch.object(physical_operation_stock, "post_operation", return_value="STE-AGG") as post:
			result = physical_operation_stock._post_rollwise_first_operation("LOT", "JOB", "ENTRY", "factory")
		self.assertEqual(result, "STE-AGG")
		post.assert_called_once_with("LOT", "JOB", "ENTRY", None, "factory")

	def test_unrecorded_process_loss_is_not_silently_written_off(self):
		from smart_edge_custom.manufacturing import planning
		job = frappe._dict(name="JOB", company="Company", actual_qty_meter=100, good_qty=95)
		lot = frappe._dict(name="LOT", finished_item="FG", input_production_lot="INPUT")
		with patch.object(physical_operation_stock, "should_post_operation", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=frappe._dict()), \
			patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), \
			patch.object(frappe, "get_all", return_value=[]), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(physical_operation_stock, "_batch", return_value="OUT"), \
			patch.object(physical_operation_stock, "find_physical_input_lot", return_value="INPUT"), \
			patch.object(physical_operation_stock, "physical_input_batch", return_value="IN"), \
			patch.object(physical_operation_stock, "validate_physical_batch_for_production"), \
			patch.object(frappe, "get_doc", return_value=frappe._dict()), \
			patch.object(frappe, "new_doc", return_value=Mock()), \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError("disposition")):
			with self.assertRaisesRegex(frappe.ValidationError, "disposition"):
				physical_operation_stock.post_operation(lot, job, frappe._dict(), None, Mock())

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
		with patch.object(physical_operation_stock, "should_post_operation", return_value=True), \
			patch.object(planning, "get_previous_mandatory_entry", return_value=None), \
			patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Meter")), \
			patch.object(planning, "get_required_conversion_factor", return_value=1), \
			patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), \
			patch.object(frappe, "get_all", return_value=[]), \
			patch.object(frappe, "new_doc", side_effect=lambda dt: StockEntry()), \
			patch.object(frappe, "get_doc", side_effect=lambda dt, name: generator if dt == "Final EB BOM Generator" else batch), \
			patch.object(physical_operation_stock, "_wip_warehouse", return_value="WIP"), \
			patch.object(physical_operation_stock, "_batch", side_effect=["EXT", "QC"]), \
			patch.object(physical_operation_stock, "physical_input_batch", return_value="EXT"), \
			patch.object(physical_operation_stock, "validate_physical_batch_for_production"), \
			patch.object(physical_operation_stock, "find_physical_input_lot", side_effect=[None, "EXT-LOT"]), \
			patch.object(physical_operation_stock, "_batch_source", return_value="WIP"), \
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
			# Physical quantity is evidenced by the posted in-memory ledger balance;
			# Batch.batch_qty is not a writable source of inventory truth.
			self.assertGreater(balances["EXT"], 0)
			self.assertEqual(entries[0].purpose, "Manufacture")
			self.assertEqual((balances["CMP"], balances["BASE"]), (0, 0))
			lot.input_production_lot = "EXT-LOT"
			job.name, job.good_qty, job.actual_qty_meter = "QC-JOB", 30, 30
			physical_operation_stock.post_operation(lot, job, frappe._dict(), None, Mock())
			self.assertAlmostEqual(balances["EXT"], 9.784206)
			self.assertEqual(balances["QC"], 30)
			self.assertEqual(entries[1].purpose, "Repack")
			self.assertEqual(materials.call_count, 1)

	def test_incremental_packing_repack_consumes_only_physical_wip(self):
		"""A later Packing execution moves WIP into the existing FG Batch."""
		entries = []
		class StockEntry:
			def __init__(self):
				self.items = []
				self.name = "STE-REPACK-1"
			def set_stock_entry_type(self):
				self.stock_entry_type = self.purpose
			def append(self, field, values):
				self.items.append(frappe._dict(values))
			def insert(self, **kwargs):
				for row in self.items:
					row.transfer_qty = row.qty * row.conversion_factor
				entries.append(self)
			def save(self, **kwargs):
				pass
			def submit(self):
				self.docstatus = 1

		generator = frappe._dict(name="GEN", company="Company", finished_item="FG")
		job = frappe._dict(name="PACK-2", good_qty=3000, actual_qty_meter=3000,
			input_production_lot="QC-LOT", plan_entry="PLAN")
		output = frappe._dict(t_warehouse="Finished Goods", uom="Meter", stock_uom="Meter",
			conversion_factor=1)
		def bundle(stock, row, batch, qty, warehouse, direction):
			row.batch_no = batch
		fake_frappe = Mock()
		fake_frappe.new_doc.side_effect = lambda doctype: StockEntry()
		fake_frappe.get_all.return_value = []
		fake_frappe.db.exists.return_value = False
		with patch.object(physical_operation_stock, "physical_input_batch", return_value="WIP-BATCH"), \
			patch.object(physical_operation_stock, "_batch_source", return_value="WIP"), \
			patch.object(physical_operation_stock, "stock_row", side_effect=lambda item, qty, warehouse, inward=False: {
				"item_code": item, "qty": qty, "uom": "Meter", "stock_uom": "Meter",
				"conversion_factor": 1, ("t_warehouse" if inward else "s_warehouse"): warehouse,
			}), \
			patch.object(physical_operation_stock, "validate_posted_precision"), \
			patch.object(physical_operation_stock, "_bundle", side_effect=bundle), \
			patch.object(physical_operation_stock, "frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.batch_genealogy.capture_stock_entry"), \
			patch.object(physical_operation_stock, "nowdate", return_value="2026-11-03"):
			name = physical_operation_stock.post_incremental_packing_repack(generator, job, output, "FG-BATCH")

		self.assertEqual(name, "STE-REPACK-1")
		self.assertEqual(entries[0].purpose, "Repack")
		self.assertEqual(len(entries[0].items), 2)
		self.assertEqual(entries[0].items[0].s_warehouse, "WIP")
		self.assertEqual(entries[0].items[1].t_warehouse, "Finished Goods")
		self.assertEqual(entries[0].items[0].batch_no, "WIP-BATCH")
		self.assertEqual(entries[0].items[1].batch_no, "FG-BATCH")

	def test_incremental_packing_resolves_completed_physical_predecessor_through_qc(self):
		job = frappe._dict(name="PACK-1", plan_entry="PACK-PLAN")
		entry = frappe._dict(name="PACK-PLAN", released_from_production_job="QC-JOB", final_eb_bom_generator="GEN", plan_type="Final EB", sequence=3)
		fake_frappe = Mock()
		fake_frappe.get_doc.return_value = entry
		fake_frappe.get_all.return_value = [frappe._dict(name="EXT-PLAN", operation_row="EXT-ROW", sequence=1)]
		fake_frappe.db.get_value.side_effect = lambda doctype, key, field, **kwargs: (
			None if isinstance(key, str) and key == "QC-JOB" and field == "output_production_lot"
			else "QC-JOB" if isinstance(key, dict) and key.get("plan_entry") == "QC-PLAN" and field == "name"
			else None if isinstance(key, str) and key == "QC-JOB" and field == "output_production_lot"
			else "EXT-LOT" if isinstance(key, dict) and key.get("plan_entry") == "EXT-PLAN" and field == "output_production_lot"
			else "EXT-LOT" if isinstance(key, str) and key == "EXT-JOB" and field == "output_production_lot"
			else None
		)
		with patch.object(physical_operation_stock, "frappe", fake_frappe), \
			patch("smart_edge_custom.manufacturing.planning.is_mandatory_operation", return_value=True):
			self.assertEqual(physical_operation_stock.resolve_packing_input_lot(job), "EXT-LOT")

	def test_incremental_repack_retry_reuses_submitted_entry(self):
		fake_frappe = Mock()
		fake_frappe.get_all.return_value = [frappe._dict(name="STE-REPACK-1", docstatus=1)]
		fake_frappe.db.exists.return_value = False
		generator = frappe._dict(name="GEN", company="Company", finished_item="FG")
		job = frappe._dict(name="PACK-2", good_qty=3000, actual_qty_meter=3000)
		with patch.object(physical_operation_stock, "frappe", fake_frappe):
			name = physical_operation_stock.post_incremental_packing_repack(
				generator, job, frappe._dict(t_warehouse="FG"), "FG-BATCH"
			)
		self.assertEqual(name, "STE-REPACK-1")
		fake_frappe.new_doc.assert_not_called()

	def test_rejected_disposition_fails_closed_without_qc_roll_evidence(self):
		with patch.object(physical_operation_stock, "enabled", return_value=True), \
			patch.object(frappe, "throw", side_effect=frappe.ValidationError("QC inspection and rejected roll quantities")):
			with self.assertRaisesRegex(frappe.ValidationError, "QC inspection"):
				physical_operation_stock._validated_rejected_roll_quantity(
					"LOT", None, None, "WIP-BATCH", 10
				)

	def test_physical_incremental_reference_is_not_accepted_as_arbitrary_repack(self):
		from smart_edge_custom.manufacturing import planning
		db = Mock()
		db.get_value.side_effect = ["SmartEdge unrelated Repack", "Repack"]
		with patch.object(planning.frappe, "db", db):
			self.assertFalse(planning._is_valid_incremental_packing_stock_entry("STE-1", "PACK-1"))

	def test_genealogy_resolves_physical_incremental_packing_job(self):
		from smart_edge_custom.manufacturing import batch_genealogy
		db = Mock()
		db.get_value.return_value = frappe._dict(name="PACK-2", plan_entry="PLAN", final_eb_bom_generator="GEN")
		stock_entry = frappe._dict(remarks="SmartEdge incremental Packing physical Repack PACK-2")
		with patch.object(batch_genealogy.frappe, "db", db):
			job = batch_genealogy._incremental_packing_job(stock_entry)
		self.assertEqual(job.name, "PACK-2")

	def test_existing_packing_fg_batch_loads_stock_entry_before_extracting_movements(self):
		generator = frappe._dict(name="GEN", finished_item="FG")
		stock_entry = frappe._dict(name="STE-REPACK-1", purpose="Repack", docstatus=1, items=[])
		with patch.object(physical_operation_stock.frappe, "get_all", side_effect=[
			[frappe._dict(name="PACK-1")],
			[],
			[frappe._dict(name="STE-REPACK-1")],
		]), patch.object(physical_operation_stock.frappe, "get_doc", return_value=stock_entry) as get_doc, \
			patch("smart_edge_custom.manufacturing.batch_genealogy.extract_batch_movements", return_value=[
				{"direction": "Output", "item_code": "FG", "batch_no": "FG-BATCH"},
			]) as extract:
			self.assertEqual(physical_operation_stock.get_packing_fg_batch(generator), "FG-BATCH")
		get_doc.assert_called_once_with("Stock Entry", "STE-REPACK-1")
		extract.assert_called_once_with(stock_entry)

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
