import unittest
import logging
import importlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job import smartedge_production_job as job_module


class TestPackingCompletionOrder(unittest.TestCase):
	def test_physical_packing_uses_current_execution_quantity_fields(self):
		planning = importlib.import_module("smart_edge_custom.manufacturing.planning")
		job = frappe._dict(
			name="PACK-1", status="Completed", plan_entry="PLAN-PACK",
			final_eb_bom_generator="FEBG-TEST", good_qty=14, actual_qty_meter=14,
		)
		job.db_set = Mock()
		entry = frappe._dict(operation="Packing", planning_category="Packing")
		generator = frappe._dict(name="FEBG-TEST", finished_item="FG", company="Company")
		meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "stock_posted_qty")
		job.meta = meta
		db = Mock()
		with patch.object(planning.frappe, "get_doc", side_effect=lambda doctype, name: {
				"SmartEdge Production Job": job,
				"SmartEdge Production Plan Entry": entry,
				"Final EB BOM Generator": generator,
			}[doctype]), \
			patch.object(planning.frappe, "db", db), \
			patch.object(planning.frappe, "get_meta", return_value=meta), \
			patch.object(planning, "lock_final_eb_bom_generator"), \
			patch.object(planning, "get_finished_stock_row", return_value=frappe._dict(t_warehouse="Finished Goods")), \
			patch.object(planning, "physical_operation_route_active", return_value=True), \
			patch("smart_edge_custom.manufacturing.physical_operation_stock.production_uses_physical_stock", return_value=True), \
			patch("smart_edge_custom.manufacturing.physical_operation_stock.get_packing_fg_batch", return_value=None), \
			patch("smart_edge_custom.manufacturing.physical_operation_stock.post_incremental_packing_repack", return_value="STE-1") as post, \
			patch("smart_edge_custom.manufacturing.roll_tracking.link_packed_rolls_to_finished_batch"), \
			patch("smart_edge_custom.manufacturing.production_lot.reconcile_final_production"):
			self.assertEqual(planning.post_completed_physical_packing(job.name), "STE-1")
		post.assert_called_once()
		self.assertEqual(post.call_args.args[2].t_warehouse, "Finished Goods")
		job.db_set.assert_any_call("stock_posted_qty", 14, update_modified=False)
	def run_sync(self, deferred):
		job = frappe._dict(
			plan_entry="PLAN-PACK",
			final_eb_bom_generator="FEBG-TEST",
		)
		db = Mock()
		db.get_value.return_value = frappe._dict(
			plan_type="Final EB", operation="Packing", planning_category="Packing",
		)
		meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "production_job")
		frappe.local.flags = frappe._dict(smartedge_defer_packing_reconciliation=deferred)
		with patch.object(job_module.frappe, "db", db), \
			patch.object(job_module.frappe, "get_meta", return_value=meta), \
			patch.object(frappe, "logger", return_value=logging.getLogger("packing-completion-test")), \
			patch("frappe.translate.get_all_translations", return_value={}):
			planning = importlib.import_module("smart_edge_custom.manufacturing.planning")
			with patch.object(planning, "update_generator_planning_status") as update:
				job_module.SmartEdgeProductionJob.sync_plan_entry(job, "Completed")
		return db, update

	def test_packing_sync_defers_generator_reconciliation(self):
		_, update = self.run_sync(True)
		update.assert_not_called()

	def test_non_deferred_packing_sync_reconciles_generator(self):
		_, update = self.run_sync(False)
		update.assert_called_once_with("FEBG-TEST")


if __name__ == "__main__":
	unittest.main()
