from unittest import TestCase
from unittest.mock import patch

import frappe

from smart_edge_custom.manufacturing import final_bom_generator
from smart_edge_custom.manufacturing import material_production


class ProductionQuantityRequirementTests(TestCase):
	class Blocked(Exception):
		pass

	def make_doc(self, production_quantity, required_quantity=10000, **values):
		return frappe._dict(
			{
				"production_quantity": production_quantity,
				"required_production_quantity": required_quantity,
				"stock_uom": "m",
				"sales_order": "SO-1",
				**values,
			}
		)

	def assert_blocked(self, production_quantity, required_quantity=10000):
		with patch.object(
			final_bom_generator.frappe,
			"throw",
			side_effect=self.Blocked,
		) as throw:
			with self.assertRaises(self.Blocked):
				final_bom_generator.validate_production_quantity_requirement(
					self.make_doc(production_quantity, required_quantity)
				)

		message = throw.call_args.args[0]
		self.assertEqual(throw.call_args.kwargs["title"], "Insufficient Production Quantity")
		self.assertIn("Required Production Quantity: 10,000 m", message)
		self.assertIn(f"Current Production Quantity: {production_quantity:,} m", message)
		self.assertIn(f"Shortfall: {required_quantity - production_quantity:,} m", message)

	def test_lower_quantities_are_blocked(self):
		for production_quantity in (0, 8000, 9999):
			with self.subTest(production_quantity=production_quantity):
				self.assert_blocked(production_quantity)

	def test_equal_and_higher_quantities_are_allowed(self):
		for production_quantity in (10000, 11000):
			with self.subTest(production_quantity=production_quantity):
				result = final_bom_generator.validate_production_quantity_requirement(
					self.make_doc(production_quantity)
				)
				self.assertEqual(result["shortfall"], 0)

	def test_sales_order_reference_rows_are_authoritative(self):
		doc = self.make_doc(
			9999,
			required_quantity=1,
			sales_order_references=[
				frappe._dict(converted_production_quantity=6000),
				frappe._dict(converted_production_quantity=4000),
			],
		)
		with patch.object(final_bom_generator.frappe, "throw", side_effect=self.Blocked) as throw:
			with self.assertRaises(self.Blocked):
				final_bom_generator.validate_production_quantity_requirement(doc)
		self.assertIn("Shortfall: 1 m", throw.call_args.args[0])

	def test_manual_generator_without_requirement_keeps_existing_workflow(self):
		doc = self.make_doc(0, required_quantity=0, sales_order=None)
		result = final_bom_generator.validate_production_quantity_requirement(doc)
		self.assertEqual(result["required_production_quantity"], 0)

	def test_sales_order_without_requirement_is_rejected(self):
		doc = self.make_doc(10000, required_quantity=0)
		with patch.object(final_bom_generator.frappe, "throw", side_effect=self.Blocked) as throw:
			with self.assertRaises(self.Blocked):
				final_bom_generator.validate_production_quantity_requirement(doc)
		self.assertEqual(throw.call_args.kwargs["title"], "Production Quantity Requirement Unavailable")

	def test_required_quantity_without_uom_is_rejected(self):
		doc = self.make_doc(10000, stock_uom=None, finished_item=None)
		with patch.object(final_bom_generator.frappe, "throw", side_effect=self.Blocked) as throw:
			with self.assertRaises(self.Blocked):
				final_bom_generator.validate_production_quantity_requirement(doc)
		self.assertEqual(throw.call_args.kwargs["title"], "Production Quantity Requirement Unavailable")

	def test_material_plan_creation_validates_before_work_order_creation(self):
		for material_type in ("Compound", "Base Colour"):
			with self.subTest(material_type=material_type):
				requirement = {
					"material_type": material_type,
					"batches_to_plan": 2,
					"bom_no": "BOM-MATERIAL-1",
					"item_code": "MATERIAL-1",
				}
				with patch.object(
					material_production,
					"validate_production_quantity_requirement",
					side_effect=self.Blocked,
				), patch.object(material_production, "make_batch_work_order") as make_work_order:
					with self.assertRaises(self.Blocked):
						material_production.create_material_plan(
							frappe._dict(name="FEBG-1"), requirement, {}
						)
				make_work_order.assert_not_called()

	def test_send_to_planning_validates_before_existing_plan_lookup(self):
		doc = frappe._dict(name="FEBG-1", reload=lambda: None)
		with patch.object(
			final_bom_generator,
			"lock_generator_for_planning",
		), patch.object(
			final_bom_generator,
			"validate_production_quantity_requirement",
			side_effect=self.Blocked,
		), patch.object(final_bom_generator, "get_existing_final_planning_entries") as existing:
			with self.assertRaises(self.Blocked):
				final_bom_generator.send_generator_to_planning(doc)
		existing.assert_not_called()
