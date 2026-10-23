from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from smart_edge_custom.base_colour import calculate_recipe_total, get_conversion_factor
from smart_edge_custom.manufacturing.final_bom_generator import (
	get_prepared_component_batch_weight,
	get_stock_balance_in_kg,
)


class TestBaseColourUOM(IntegrationTestCase):
	@patch("smart_edge_custom.base_colour.frappe.db.get_value")
	@patch("smart_edge_custom.base_colour.get_base_colour_item_details")
	@patch("smart_edge_custom.base_colour.get_conversion_factor", return_value=0.001)
	def test_gram_recipe_is_totalled_in_kg(self, _get_factor, get_base_item, get_value):
		get_base_item.return_value = frappe._dict({"stock_uom": "Kg"})
		get_value.return_value = frappe._dict(
			{"item_group": "Pigments", "stock_uom": "Gram"}
		)

		result = calculate_recipe_total(
			"Pumic Grey",
			[
				{"pigment_item": "Orange", "quantity_kg": 250},
				{"pigment_item": "Yellow", "quantity_kg": 50},
			],
		)

		self.assertEqual(result, {"total": 0.3, "stock_uom": "Kg"})

	@patch("smart_edge_custom.base_colour.frappe.db.get_value")
	@patch("smart_edge_custom.base_colour.get_base_colour_item_details")
	@patch("smart_edge_custom.base_colour.get_conversion_factor")
	def test_mixed_recipe_is_totalled_in_finished_item_stock_uom(
		self, get_factor, get_base_item, get_value
	):
		get_base_item.return_value = frappe._dict({"stock_uom": "Gram"})
		get_value.side_effect = [
			frappe._dict({"item_group": "Pigments", "stock_uom": "Gram"}),
			frappe._dict({"item_group": "Pigments", "stock_uom": "Kg"}),
		]
		get_factor.side_effect = lambda source, target: {
			("Gram", "Gram"): 1,
			("Kg", "Gram"): 1000,
		}[(source, target)]

		result = calculate_recipe_total(
			"Pumic Grey",
			[
				{"pigment_item": "Orange", "quantity_kg": 250},
				{"pigment_item": "Yellow", "quantity_kg": 0.05},
			],
		)

		self.assertEqual(result, {"total": 300.0, "stock_uom": "Gram"})

	@patch("smart_edge_custom.base_colour.get_uom_conv_factor", return_value=0.001)
	def test_gram_to_kg_uses_erpnext_conversion_factor(self, get_factor):
		self.assertEqual(get_conversion_factor("Gram", "Kg"), 0.001)
		get_factor.assert_called_once_with("Gram", "Kg")

	@patch(
		"smart_edge_custom.manufacturing.final_bom_generator.get_kg_to_stock_factor",
		return_value=1000,
	)
	@patch("smart_edge_custom.manufacturing.final_bom_generator.frappe.db.get_value")
	@patch(
		"smart_edge_custom.manufacturing.final_bom_generator.get_base_colour_stock_item",
		return_value="Pumic Grey",
	)
	def test_base_colour_batch_weight_is_normalized_to_kg(
		self, _get_item, get_value, get_factor
	):
		get_value.side_effect = [300, "Gram"]

		self.assertEqual(
			get_prepared_component_batch_weight("Base Colour", "Pumic Grey", "Shade"), 0.3
		)
		get_factor.assert_called_once_with("Pumic Grey", "Gram")

	@patch("smart_edge_custom.manufacturing.final_bom_generator.get_stock_balance", return_value=1500)
	@patch("smart_edge_custom.manufacturing.final_bom_generator.frappe.db.get_value", return_value=1000)
	def test_gram_stock_balance_is_converted_to_kg(self, _get_value, _get_balance):
		self.assertEqual(get_stock_balance_in_kg("Pumic Grey", "Gram", "Stores"), 1.5)
