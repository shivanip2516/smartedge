import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing.planning import (
	get_previous_mandatory_entry,
	normalize_schedule_time,
	validate_material_plan_released,
	validate_material_scheduling_context,
	validate_predecessor_release,
)


class TestPredecessorReleaseValidation(unittest.TestCase):
	def make_entry(self, sequence=1, plan_type="Final EB", **values):
		entry = frappe._dict(
			{
				"name": f"PLAN-{sequence}",
				"final_eb_bom_generator": "FEBG-TEST",
				"plan_type": plan_type,
				"operation": f"Operation {sequence}",
				"sequence": sequence,
				"released_qty_meter": 0,
				"released_qty_kg": 0,
			}
		)
		entry.update(values)
		return entry

	@patch("smart_edge_custom.manufacturing.planning.frappe.get_all", return_value=[])
	def test_first_final_operation_excludes_required_material_entries(self, get_all):
		entry = self.make_entry(sequence=1)

		self.assertIsNone(get_previous_mandatory_entry(entry))

		filters = get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["final_eb_bom_generator"], "FEBG-TEST")
		self.assertEqual(filters["plan_type"], "Final EB")
		self.assertEqual(filters["sequence"], ["<", 1])

	@patch("smart_edge_custom.manufacturing.planning.frappe.get_all")
	@patch("smart_edge_custom.manufacturing.planning.is_mandatory_operation", return_value=True)
	def test_downstream_operation_still_finds_predecessor(self, _is_mandatory, get_all):
		predecessor = frappe._dict(
			{"name": "PLAN-1", "operation": "Operation 1", "operation_row": "ROUTE-OP-1", "sequence": 1}
		)
		get_all.return_value = [predecessor]

		self.assertEqual(get_previous_mandatory_entry(self.make_entry(sequence=2)), predecessor)

	@patch("smart_edge_custom.manufacturing.planning.get_material_start_predecessors", return_value=[])
	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry", return_value=None)
	def test_genuine_first_operation_does_not_require_release(self, _get_previous, _material_previous):
		validate_predecessor_release(self.make_entry(sequence=1))

	@patch("smart_edge_custom.manufacturing.planning.get_material_start_predecessors")
	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry")
	def test_downstream_operation_without_release_is_blocked(self, get_previous, material_previous):
		get_previous.return_value = frappe._dict({"name": "PLAN-1", "operation": "Operation 1"})
		material_previous.return_value = []

		with self.assertRaises(frappe.ValidationError):
			validate_predecessor_release(self.make_entry(sequence=2))

	@patch("smart_edge_custom.manufacturing.planning.get_material_start_predecessors", return_value=[])
	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry")
	def test_downstream_operation_with_partial_release_is_allowed(self, get_previous, _material_previous):
		get_previous.return_value = frappe._dict({"name": "PLAN-1", "operation": "Operation 1"})

		validate_predecessor_release(self.make_entry(sequence=2, released_qty_meter=400))

	@patch("smart_edge_custom.manufacturing.planning.get_material_start_predecessors")
	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry", return_value=None)
	def test_first_finished_operation_waits_for_linked_material_completion(
		self, _get_previous, material_previous
	):
		material_previous.return_value = [
			frappe._dict({"operation": "MIXING", "status": "Pending"})
		]

		with self.assertRaisesRegex(frappe.ValidationError, "linked material operation MIXING"):
			validate_predecessor_release(self.make_entry(sequence=1))

	@patch("smart_edge_custom.manufacturing.planning.get_material_start_predecessors")
	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry", return_value=None)
	def test_first_finished_operation_allows_completed_linked_materials(
		self, _get_previous, material_previous
	):
		material_previous.return_value = [
			frappe._dict({"operation": "MIXING", "status": "Completed"})
		]

		validate_predecessor_release(self.make_entry(sequence=1))

	@patch("smart_edge_custom.manufacturing.planning.frappe.get_all")
	def test_required_material_first_operation_has_no_route_predecessor(self, get_all):
		self.assertIsNone(get_previous_mandatory_entry(self.make_entry(plan_type="Required Material")))
		get_all.assert_not_called()

	@patch("smart_edge_custom.manufacturing.planning.frappe.db.exists", return_value=False)
	def test_internal_material_plan_cannot_be_scheduled_before_release(self, _exists):
		entry = self.make_entry(plan_type="Required Material", material_type="Compound")

		with self.assertRaisesRegex(frappe.ValidationError, "Send Final EB BOM Generator"):
			validate_material_plan_released(entry)

	@patch("smart_edge_custom.manufacturing.planning.frappe.db.exists", return_value=True)
	def test_linked_material_plan_can_be_scheduled_after_release(self, _exists):
		entry = self.make_entry(plan_type="Required Material", material_type="Compound")

		validate_material_plan_released(entry)

	def test_schedule_time_accepts_minutes_and_preserves_seconds(self):
		self.assertEqual(normalize_schedule_time("14:30"), "14:30:00")
		self.assertEqual(normalize_schedule_time("14:30:45"), "14:30:45")

	@patch("smart_edge_custom.manufacturing.planning.frappe.db.get_value")
	def test_material_scheduling_uses_matching_work_order_and_bom(self, get_value):
		get_value.return_value = frappe._dict(
			{
				"docstatus": 1,
				"status": "Not Started",
				"production_item": "COMPOUND",
				"bom_no": "BOM-COMPOUND",
				"qty": 129.7,
				"custom_final_eb_bom_generator": "FEBG-TEST",
				"custom_material_type": "Compound",
			}
		)
		entry = self.make_entry(
			plan_type="Required Material",
			material_type="Compound",
			material_item="COMPOUND",
			bom_no="BOM-COMPOUND",
			work_order="WO-COMPOUND",
		)

		validate_material_scheduling_context(entry)

	@patch("smart_edge_custom.manufacturing.planning.frappe.db.get_value")
	def test_material_scheduling_rejects_mismatched_output_item(self, get_value):
		get_value.return_value = frappe._dict(
			{
				"docstatus": 1,
				"status": "Not Started",
				"production_item": "OTHER-ITEM",
				"bom_no": "BOM-COMPOUND",
				"qty": 129.7,
			}
		)
		entry = self.make_entry(
			plan_type="Required Material",
			material_type="Compound",
			material_item="COMPOUND",
			bom_no="BOM-COMPOUND",
			work_order="WO-COMPOUND",
		)

		with self.assertRaisesRegex(frappe.ValidationError, "does not match"):
			validate_material_scheduling_context(entry)


if __name__ == "__main__":
	unittest.main()
