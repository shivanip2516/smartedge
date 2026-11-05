import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.smart_edge_custom.doctype.smartedge_production_job import smartedge_production_job

parse_output_quantity = smartedge_production_job.parse_output_quantity


class TestPhase6OutputCapture(unittest.TestCase):
	def setUp(self):
		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)

		self.enterContext(patch.object(frappe, "throw", side_effect=throw))
		self.enterContext(patch.object(smartedge_production_job, "_", side_effect=lambda text: text))
		self.enterContext(
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding (legacy)")
		)

	def test_requested_output_values_preserve_six_decimal_precision(self):
		self.assertEqual(parse_output_quantity("950.1234567", "Good Output"), 950.123457)
		self.assertEqual(parse_output_quantity(0, "Rejected Output"), 0)

	def test_negative_output_is_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Rejected Output cannot be negative"):
			parse_output_quantity(-5, "Rejected Output")

	def test_non_numeric_and_non_finite_output_are_rejected(self):
		for value in ("invalid", "NaN", "Infinity"):
			with self.subTest(value=value), self.assertRaisesRegex(
				frappe.ValidationError, "Usable Scrap must be a valid number"
			):
				parse_output_quantity(value, "Usable Scrap")


if __name__ == "__main__":
	unittest.main()
