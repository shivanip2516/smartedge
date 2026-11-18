import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing.planning import validate_roll_packing_details


class TestRollPackingValidation(unittest.TestCase):
	def setUp(self):
		self.entry = frappe._dict(planning_category="Extrusion", operation_row=None)
		self.job = frappe._dict(roll_packing_details=[])

	def test_valid_whole_meter_rows_are_accepted(self):
		rows = validate_roll_packing_details(
			self.entry,
			self.job,
			3,
			[
				{"roll_no": 1, "meter_quantity": 10000},
				{"roll_no": 2, "meter_quantity": 100},
				{"roll_no": 3, "meter_quantity": 2000},
			],
			13146,
		)
		self.assertEqual(sum(row["meter_quantity"] for row in rows), 12100)

	def test_total_cannot_exceed_current_actual_completion(self):
		self.assert_invalid(
			[
				{"roll_no": 1, "meter_quantity": 10000},
				{"roll_no": 2, "meter_quantity": 100},
				{"roll_no": 3, "meter_quantity": 2000},
			],
			actual=10000,
			roll_count=3,
		)

	def assert_invalid(self, details, actual=10000, roll_count=1):
		with patch("smart_edge_custom.manufacturing.planning.frappe.throw", side_effect=frappe.ValidationError("invalid")):
			with self.assertRaises(frappe.ValidationError):
				validate_roll_packing_details(self.entry, self.job, roll_count, details, actual)

	def test_total_cannot_exceed_actual_completion(self):
		self.assert_invalid(
			[{"roll_no": 1, "meter_quantity": 11000}],
			roll_count=1,
		)

	def test_zero_fractional_and_mismatched_rows_are_rejected(self):
		self.assert_invalid([{"roll_no": 1, "meter_quantity": 0}])
		self.assert_invalid([{"roll_no": 1, "meter_quantity": 10.5}])
		self.assert_invalid([{"roll_no": 1, "meter_quantity": 5000}], roll_count=2)

	def test_non_extrusion_operations_do_not_accept_roll_details(self):
		entry = frappe._dict(planning_category="Packing", operation_row=None)
		with patch("smart_edge_custom.manufacturing.planning.frappe.throw", side_effect=frappe.ValidationError("invalid")):
			with self.assertRaises(frappe.ValidationError):
				validate_roll_packing_details(entry, self.job, 1, [{"roll_no": 1, "meter_quantity": 1}], 1)


if __name__ == "__main__":
	unittest.main()
