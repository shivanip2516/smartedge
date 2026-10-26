import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing.planning import (
	get_previous_mandatory_entry,
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

	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry", return_value=None)
	def test_genuine_first_operation_does_not_require_release(self, _get_previous):
		validate_predecessor_release(self.make_entry(sequence=1))

	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry")
	def test_downstream_operation_without_release_is_blocked(self, get_previous):
		get_previous.return_value = frappe._dict({"name": "PLAN-1", "operation": "Operation 1"})

		with self.assertRaises(frappe.ValidationError):
			validate_predecessor_release(self.make_entry(sequence=2))

	@patch("smart_edge_custom.manufacturing.planning.get_previous_mandatory_entry")
	def test_downstream_operation_with_partial_release_is_allowed(self, get_previous):
		get_previous.return_value = frappe._dict({"name": "PLAN-1", "operation": "Operation 1"})

		validate_predecessor_release(self.make_entry(sequence=2, released_qty_meter=400))

	@patch("smart_edge_custom.manufacturing.planning.frappe.get_all")
	def test_required_material_first_operation_has_no_route_predecessor(self, get_all):
		self.assertIsNone(get_previous_mandatory_entry(self.make_entry(plan_type="Required Material")))
		get_all.assert_not_called()


if __name__ == "__main__":
	unittest.main()
