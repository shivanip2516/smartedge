"""Availability checks use temporary BOMs and never create stock movements."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from smart_edge_custom import test_bom_item_groups as bom_tests
from smart_edge_custom import test_generator_item_links as generator_tests
from smart_edge_custom.manufacturing.batch_manufacturing import make_batch_work_order
from smart_edge_custom.manufacturing.material_production import (
	build_material_requirement,
	get_material_raw_stock,
	get_raw_material_availability,
)


class TestBaseColourRawStock(unittest.TestCase):
	make_bom = bom_tests.TestBOMItemGroups.make_bom
	make_component = bom_tests.TestBOMItemGroups.make_component
	source = generator_tests.TestGeneratorItemLinks.source
	generator = generator_tests.TestGeneratorItemLinks.generator

	def setUp(self):
		bom_tests.TestBOMItemGroups.setUp(self)
		self.warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")
		colour = frappe.get_doc("Item", self.items["Base Colour"])
		colour.stock_uom = "Gram"
		colour.has_batch_no = 0
		colour.create_new_batch = 0
		colour.set("uoms", [{"uom": "Gram", "conversion_factor": 1}])
		colour.save(ignore_permissions=True)
		pigment = self.make_component("Pigments", "Gram")
		self.pigment = pigment.name
		bom = self.make_bom("Base Colour", "Pigments")
		bom.items[0].item_code = pigment.name
		bom.items[0].qty = 22
		self.bom = bom.insert().submit().name
		self.make_bom("Compounds", "Additives").insert().submit()
		self.doc = self.generator()

	def test_unbatched_base_colour_shows_pigments_and_configuration_error(self):
		before = (frappe.db.count("Work Order"), frappe.db.count("Stock Entry"), frappe.db.count("Batch"))
		stock = get_material_raw_stock(self.doc.name, "Base Colour", self.warehouse)
		self.assertEqual(stock["available"], 0)
		self.assertIn("not batch-controlled", stock["message"])
		self.assertEqual(stock["materials"][0]["item_code"], self.pigment)
		self.assertEqual(stock["materials"][0]["uom"], "Gram")
		self.assertEqual(stock["materials"][0]["required_qty"], 66)
		self.assertEqual(stock["materials"][0]["shortage_qty"], 66)
		self.assertEqual(before, (frappe.db.count("Work Order"), frappe.db.count("Stock Entry"), frappe.db.count("Batch")))
		self.assertEqual(frappe.db.get_value("Item", self.doc.base_colour_item, "has_batch_no"), 0)
		with self.assertRaisesRegex(frappe.ValidationError, "not applicable"):
			make_batch_work_order(self.bom, self.doc.base_colour_item, 3, self.doc.company)

	def test_valid_base_colour_respects_reservations_and_gram_quantities(self):
		# Only this temporary fixture is batch-controlled; the production Item is untouched.
		frappe.db.set_value("Item", self.doc.base_colour_item, "has_batch_no", 1)
		get_value = frappe.db.get_value
		for reserved, available, shortage in ((10, 1, 0), (80, 0, 46)):
			def bin_value(doctype, *args, **kwargs):
				if doctype == "Bin":
					return frappe._dict(actual_qty=100, reserved_qty_for_production=reserved)
				return get_value(doctype, *args, **kwargs)
			with self.subTest(reserved=reserved), patch.object(frappe.db, "get_value", side_effect=bin_value):
				stock = get_material_raw_stock(self.doc.name, "Base Colour", self.warehouse)
			self.assertEqual(stock["available"], available)
			self.assertNotIn("message", stock)
			self.assertEqual(stock["materials"][0]["required_qty"], 66)
			self.assertEqual(stock["materials"][0]["available_qty"], 100 - reserved)
			self.assertEqual(stock["materials"][0]["shortage_qty"], shortage)

	def test_compound_remains_unchanged_with_base_colour_in_same_generator(self):
		stock = get_material_raw_stock(self.doc.name, "Compound", self.warehouse)
		self.assertNotIn("message", stock)
		self.assertEqual(stock["materials"][0]["item_code"], self.items["Additives"])
		self.assertEqual(stock["materials"][0]["uom"], "Kg")
		self.assertEqual(stock["materials"][0]["required_qty"], 6)
		colour = get_material_raw_stock(self.doc.name, "Base Colour", self.warehouse)
		self.assertEqual(colour["materials"][0]["required_qty"], 66)

	def test_missing_bom_is_reported(self):
		frappe.db.set_value("BOM", self.bom, "is_active", 0)
		with self.assertRaisesRegex(frappe.ValidationError, "active submitted one-batch BOM is required"):
			get_material_raw_stock(self.doc.name, "Base Colour", self.warehouse)
		stock = get_raw_material_availability(self.doc, {"material_type": "Base Colour", "item_code": self.doc.base_colour_item, "batches_to_plan": 3}, self.warehouse)
		self.assertEqual(stock["available"], 0)
		self.assertIn("No active submitted", stock["message"])

	def test_missing_pigments_is_reported(self):
		requirement = build_material_requirement(self.doc, "Base Colour")
		work_order = MagicMock()
		work_order.required_items = []
		with patch("erpnext.manufacturing.doctype.work_order.work_order.make_work_order", return_value=work_order):
			stock = get_raw_material_availability(self.doc, requirement, self.warehouse)
		self.assertEqual(stock["available"], 0)
		self.assertIn("No transferable Pigment", stock["message"])
