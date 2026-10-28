from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.manufacturing.work_order_sync import (
	get_entry_target_status,
	get_work_order_progress,
	operation_completion_is_supported,
	sync_work_order_to_planning,
)


class TestWorkOrderPlanningSynchronization(IntegrationTestCase):
	def make_progress(self, **values):
		defaults = {
			"completed": False,
			"partial": False,
			"cancelled": False,
			"manufacture_stock_entry": None,
			"verified_qty": 0,
		}
		defaults.update(values)
		return frappe._dict(defaults)

	@patch("smart_edge_custom.manufacturing.work_order_sync.frappe.get_all")
	def test_completed_requires_submitted_manufacture_quantity(self, get_all):
		work_order = frappe._dict(
			{
				"name": "WO-1",
				"docstatus": 1,
				"status": "Completed",
				"qty": 55,
				"produced_qty": 55,
			}
		)
		get_all.return_value = [frappe._dict({"name": "STE-1", "fg_completed_qty": 55})]
		progress = get_work_order_progress(work_order)
		self.assertTrue(progress.completed)
		self.assertEqual(progress.manufacture_stock_entry, "STE-1")

	@patch("smart_edge_custom.manufacturing.work_order_sync.frappe.get_all")
	def test_work_order_status_alone_does_not_confirm_completion(self, get_all):
		work_order = frappe._dict(
			{
				"name": "WO-1",
				"docstatus": 1,
				"status": "Completed",
				"qty": 55,
				"produced_qty": 55,
			}
		)
		get_all.return_value = []
		self.assertFalse(get_work_order_progress(work_order).completed)

	def test_paused_job_is_not_overwritten_by_in_process_work_order(self):
		entry = frappe._dict({"planned_date": "2026-10-23"})
		job = frappe._dict({"status": "Paused"})
		self.assertEqual(get_entry_target_status(entry, self.make_progress(), job), "On Hold")

	def test_partial_manufacturing_keeps_remaining_operation_open(self):
		entry = frappe._dict({"planned_date": "2026-10-23"})
		progress = self.make_progress(partial=True, verified_qty=110)
		self.assertEqual(get_entry_target_status(entry, progress), "On Hold")

	def test_completed_job_does_not_override_reversed_manufacturing_stock(self):
		entry = frappe._dict({"planned_date": "2026-10-23"})
		job = frappe._dict({"status": "Completed", "completion_type": "Full Completion"})
		self.assertEqual(get_entry_target_status(entry, self.make_progress(), job), "Planned")

	@patch("smart_edge_custom.manufacturing.work_order_sync.frappe.get_all")
	def test_multi_operation_requires_matching_completed_job_card(self, get_all):
		work_order = frappe._dict(
			{
				"name": "WO-1",
				"operations": [frappe._dict(), frappe._dict()],
			}
		)
		entry = frappe._dict({"operation": "Mixing"})
		get_all.return_value = [
			frappe._dict(
				{
					"status": "Completed",
					"for_quantity": 55,
					"total_completed_qty": 27.5,
				}
			)
		]
		self.assertFalse(operation_completion_is_supported(work_order, entry))

	@patch("smart_edge_custom.manufacturing.work_order_sync.apply_updates_if_changed")
	@patch("smart_edge_custom.manufacturing.work_order_sync.lock_plan_entry")
	@patch("smart_edge_custom.manufacturing.work_order_sync.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.work_order_sync.get_work_order_progress")
	def test_completed_work_order_updates_linked_material_entry_and_job(
		self, get_progress, get_all, _lock, apply_updates
	):
		work_order = frappe._dict(
			{
				"name": "WO-1",
				"operations": [],
			}
		)
		get_progress.return_value = self.make_progress(
			completed=True,
			verified_qty=55,
			manufacture_stock_entry="STE-1",
		)
		entry = frappe._dict(
			{
				"name": "PLAN-1",
				"status": "Planned",
				"plan_type": "Required Material",
				"operation": "Mixing",
				"planned_date": "2026-10-23",
				"final_eb_bom_generator": "FEBG-1",
				"manufacture_stock_entry": None,
				"weight_per_batch": 55,
			}
		)
		job = frappe._dict(
			{
				"name": "JOB-1",
				"plan_entry": "PLAN-1",
				"status": "In Progress",
				"completion_type": None,
				"actual_qty_meter": 0,
				"actual_qty_kg": 0,
				"good_qty": 0,
				"manufacturing_stock_entry": None,
				"stock_posted_qty": 0,
			}
		)
		get_all.side_effect = [[entry], [job]]
		result = sync_work_order_to_planning(
			work_order, refresh_readiness=False, publish=False
		)
		self.assertEqual(len(result["changes"]), 2)
		apply_updates.assert_any_call(
			"SmartEdge Production Plan Entry",
			"PLAN-1",
			{"status": "Completed", "manufacture_stock_entry": "STE-1"},
		)
		apply_updates.assert_any_call(
			"SmartEdge Production Job",
			"JOB-1",
			{
				"actual_qty_meter": 1,
				"actual_qty_kg": 55,
				"good_qty": 55,
				"manufacturing_stock_entry": "STE-1",
				"stock_posted_qty": 55,
				"completion_type": "Full Completion",
				"remaining_qty_meter": 0,
				"remaining_qty_kg": 0,
				"status": "Completed",
			},
		)

	@patch("smart_edge_custom.manufacturing.work_order_sync.apply_updates_if_changed")
	@patch("smart_edge_custom.manufacturing.work_order_sync.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.work_order_sync.get_work_order_progress")
	def test_repeated_completed_sync_is_idempotent(self, get_progress, get_all, apply_updates):
		work_order = frappe._dict({"name": "WO-1", "operations": []})
		get_progress.return_value = self.make_progress(
			completed=True,
			verified_qty=55,
			manufacture_stock_entry="STE-1",
		)
		entry = frappe._dict(
			{
				"name": "PLAN-1",
				"status": "Completed",
				"plan_type": "Required Material",
				"operation": "Mixing",
				"planned_date": "2026-10-23",
				"final_eb_bom_generator": "FEBG-1",
				"manufacture_stock_entry": "STE-1",
				"weight_per_batch": 55,
			}
		)
		job = frappe._dict(
			{
				"name": "JOB-1",
				"plan_entry": "PLAN-1",
				"status": "Completed",
				"completion_type": "Full Completion",
				"actual_qty_meter": 1,
				"actual_qty_kg": 55,
				"good_qty": 55,
				"manufacturing_stock_entry": "STE-1",
				"stock_posted_qty": 55,
			}
		)
		get_all.side_effect = [[entry], [job]]
		result = sync_work_order_to_planning(
			work_order, refresh_readiness=False, publish=False
		)
		self.assertEqual(result["changes"], [])
		apply_updates.assert_not_called()
