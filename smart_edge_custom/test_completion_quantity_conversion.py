"""Regression tests for production-specific scrap UOM conversion."""

import unittest
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing import planning


class TestCompletionQuantityConversion(unittest.TestCase):
	def setUp(self):
		# Keep these focused unit tests independent of Frappe's site precision/cache
		# services while exercising the production conversion formulas.
		def local_flt(value, precision=None):
			try:
				number = float(value)
			except (TypeError, ValueError):
				return 0.0
			return round(number, precision) if precision is not None else number

		self.enterContext(patch.object(planning, "flt", side_effect=local_flt))

	def test_febg_weight_per_meter_is_authoritative_for_final_output(self):
		entry = frappe._dict(plan_type="Final EB", final_eb_bom_generator="FEBG-1")
		job = frappe._dict(actual_qty_meter=100, actual_qty_kg=2)
		production = frappe._dict(weight_per_meter_kg=0.0198)

		self.assertAlmostEqual(
			planning.get_completion_output_kg_factor(entry, job, "FG", "Meter", production),
			0.0198,
			places=6,
		)

	def test_meter_scrap_uses_febg_factor_and_stock_kg_quantity(self):
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")):
			result = planning.convert_completion_quantity(
				"UNUSABLE-SCRAP", 5, "Meter", "Unusable Scrap", meter_to_kg_factor=0.0198
			)

		self.assertAlmostEqual(result["kg_quantity"], 0.099, places=6)
		self.assertAlmostEqual(result["stock_quantity"], 0.099, places=6)

	def test_both_scrap_types_and_good_output_are_calculated_once(self):
		context = frappe._dict(
			completion_output_item="FG",
			completion_output_uom="Meter",
			completion_output_kg_factor=0.0198,
			completion_meter_to_kg_factor=0.0198,
			usable_scrap_item="USABLE-SCRAP",
			unusable_scrap_item="UNUSABLE-SCRAP",
		)
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")):
			result = planning.calculate_completion_quantity_values(
				context,
				actual_qty_kg=195.15,
				rejected_qty_kg=0,
				rework_qty_kg=0,
				usable_scrap=0,
				usable_scrap_uom="Meter",
				unusable_scrap=5,
				unusable_scrap_uom="Meter",
			)

		self.assertAlmostEqual(result["usable_scrap_kg"], 0, places=6)
		self.assertAlmostEqual(result["unusable_scrap_kg"], 0.099, places=6)
		self.assertAlmostEqual(result["good_qty_kg"], 195.051, places=6)
		self.assertAlmostEqual(result["good_qty"], 9851.060606, places=6)

	def test_six_meter_unusable_scrap_calculates_expected_good_output(self):
		context = frappe._dict(
			completion_output_item="FG",
			completion_output_uom="Meter",
			completion_output_kg_factor=0.0198,
			completion_meter_to_kg_factor=0.0198,
			unusable_scrap_item="UNUSABLE-SCRAP",
		)
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")):
			result = planning.calculate_completion_quantity_values(
				context,
				actual_qty_kg=195.1488,
				rejected_qty_kg=0,
				rework_qty_kg=0,
				usable_scrap=0,
				usable_scrap_uom="Meter",
				unusable_scrap=6,
				unusable_scrap_uom="Meter",
			)

		self.assertAlmostEqual(result["unusable_scrap_kg"], 0.1188, places=6)
		self.assertAlmostEqual(result["good_qty_kg"], 195.03, places=6)

	def test_six_meter_usable_scrap_calculates_expected_good_output(self):
		context = frappe._dict(
			completion_output_item="FG",
			completion_output_uom="Meter",
			completion_output_kg_factor=0.0198,
			completion_meter_to_kg_factor=0.0198,
			usable_scrap_item="USABLE-SCRAP",
		)
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")):
			result = planning.calculate_completion_quantity_values(
				context,
				actual_qty_kg=195.1488,
				rejected_qty_kg=0,
				rework_qty_kg=0,
				usable_scrap=6,
				usable_scrap_uom="Meter",
				unusable_scrap=0,
				unusable_scrap_uom="Meter",
			)

		self.assertAlmostEqual(result["usable_scrap_kg"], 0.1188, places=6)
		self.assertAlmostEqual(result["good_qty_kg"], 195.03, places=6)

	def test_meter_scrap_can_post_to_a_non_kg_stock_uom_with_valid_kg_conversion(self):
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Gram")), \
			patch.object(planning, "get_completion_item_uom_to_stock_factor", return_value=1000):
			result = planning.convert_completion_quantity(
				"UNUSABLE-SCRAP", 5, "m", "Unusable Scrap", meter_to_kg_factor=0.0198
			)

		self.assertAlmostEqual(result["kg_quantity"], 0.099, places=6)
		self.assertAlmostEqual(result["stock_quantity"], 99, places=6)

	def test_meter_scrap_requires_a_valid_linked_febg_factor(self):
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")), \
			patch.object(planning.frappe, "throw", side_effect=frappe.ValidationError("no valid Weight / Meter")):
			with self.assertRaisesRegex(frappe.ValidationError, "no valid Weight / Meter"):
				planning.convert_completion_quantity(
					"UNUSABLE-SCRAP", 5, "Meter", "Unusable Scrap", meter_to_kg_factor=0
				)

	def test_zero_scrap_does_not_require_an_unused_uom_conversion(self):
		self.assertEqual(
			planning.convert_completion_quantity(None, 0, None, "Unusable Scrap", meter_to_kg_factor=0),
			{"stock_quantity": 0.0, "kg_quantity": 0.0},
		)

	def test_gram_scrap_keeps_existing_standard_uom_conversion(self):
		with patch.object(planning, "validate_stock_item", return_value=frappe._dict(stock_uom="Kg")), \
			patch.object(planning, "get_completion_item_uom_to_stock_factor", return_value=0.001), \
			patch.object(planning, "get_completion_item_uom_to_kg_factor", return_value=0.001):
			result = planning.convert_completion_quantity(
				"USABLE-SCRAP", 5, "Gram", "Usable Scrap", meter_to_kg_factor=0.0198
			)

		self.assertAlmostEqual(result["kg_quantity"], 0.005, places=6)
		self.assertAlmostEqual(result["stock_quantity"], 0.005, places=6)


if __name__ == "__main__":
	unittest.main()
