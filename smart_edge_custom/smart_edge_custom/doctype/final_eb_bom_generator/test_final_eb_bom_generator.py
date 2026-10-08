import frappe
import unittest
from frappe.tests.utils import FrappeTestCase

from smart_edge_custom.manufacturing.final_bom_generator import (
	build_eb_route,
	calculate_bom_preview,
)
from smart_edge_custom.setup import setup_smartedge_manufacturing


class TestFinalEBBOMGenerator(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		if not cls.company:
			raise unittest.SkipTest("A Company is required for BOM tests.")

		cls._ensure_master_data()
		setup_smartedge_manufacturing()

	@classmethod
	def _ensure_master_data(cls):
		for uom in ("Kg", "Gram", "Meter"):
			if not frappe.db.exists("UOM", uom):
				frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)

		for item_group in ("Compounds", "Base Colour", "Additives", "Pigments", "Finished Goods"):
			if not frappe.db.exists("Item Group", item_group):
				frappe.get_doc(
					{
						"doctype": "Item Group",
						"item_group_name": item_group,
						"parent_item_group": "All Item Groups",
					}
				).insert(ignore_permissions=True)

		cls._ensure_item("TEST-FINAL-EB", "Finished Goods", "Meter", custom_size="2x24")
		cls._ensure_item("TEST-COMPOUND", "Compounds", "Kg")
		cls._ensure_item("TEST-SHADE", "Base Colour", "Kg")
		cls._ensure_item("TEST-SHADE-GRAM", "Base Colour", "Gram")
		cls._ensure_item("TEST-ADDITIVE", "Additives", "Kg")
		cls._ensure_item("TEST-PIGMENT", "Pigments", "Kg")

		if not frappe.db.exists("Compound", "TEST-COMPOUND"):
			doc = frappe.new_doc("Compound")
			doc.compound_name = "TEST-COMPOUND"
			doc.append("additives", {"additive": "TEST-ADDITIVE", "uom": "Kg", "quantity_kg": 1})
			doc.insert(ignore_permissions=True)

		if not frappe.db.exists("Base Colour", "TEST-SHADE"):
			doc = frappe.new_doc("Base Colour")
			doc.colour_name = "TEST-SHADE"
			doc.weightage = 5
			doc.append("pigments", {"pigment_item": "TEST-PIGMENT", "uom": "Kg", "quantity_kg": 1})
			doc.insert(ignore_permissions=True)

		if not frappe.db.exists("Base Colour", "TEST-SHADE-GRAM"):
			doc = frappe.new_doc("Base Colour")
			doc.colour_name = "TEST-SHADE-GRAM"
			doc.weightage = 5
			doc.append("pigments", {"pigment_item": "TEST-PIGMENT", "uom": "Kg", "quantity_kg": 1})
			doc.insert(ignore_permissions=True)

		for workstation in (
			"Single Mould Extrusion",
			"Wide Sheet Extrusion",
			"80 mm Small Slitting Machine",
			"400 mm Slitting Machine",
			"Offline Printing Machine",
			"QC After Extrusion",
			"QC After Offline Printing",
			"Packing",
		):
			cls._ensure_workstation(workstation)

	@classmethod
	def _ensure_item(cls, item_code, item_group, stock_uom, **values):
		if frappe.db.exists("Item", item_code):
			return

		doc = frappe.new_doc("Item")
		doc.item_code = item_code
		doc.item_name = item_code
		doc.item_group = item_group
		doc.stock_uom = stock_uom
		doc.include_item_in_manufacturing = 1
		for fieldname, value in values.items():
			if doc.meta.has_field(fieldname):
				doc.set(fieldname, value)
		doc.insert(ignore_permissions=True)

	@classmethod
	def _ensure_workstation(cls, workstation_name):
		if frappe.db.exists("Workstation", workstation_name):
			return

		doc = frappe.new_doc("Workstation")
		doc.workstation_name = workstation_name
		doc.production_capacity = 1
		doc.insert(ignore_permissions=True)

	@classmethod
	def _ensure_operation(cls, operation_name, workstation):
		if frappe.db.exists("Operation", operation_name):
			return

		doc = frappe.new_doc("Operation")
		doc.name = operation_name
		doc.workstation = workstation
		doc.insert(ignore_permissions=True)

	def get_generator(self, **values):
		doc = frappe.new_doc("Final EB BOM Generator")
		doc.company = self.company
		doc.finished_item = "TEST-FINAL-EB"
		doc.production_quantity = 1000
		doc.thickness_mm = 2
		doc.width_mm = 24
		doc.weight_factor = 1.5
		doc.compound = "TEST-COMPOUND"
		doc.compound_consumption_per_meter = 0.066
		doc.base_colour = "TEST-SHADE"
		doc.shade_consumption_per_meter = 0.006
		doc.printing_type = "Solid"
		doc.printing_route = "None"
		doc.extrusion_type = "Single Mould Extrusion"
		doc.slitting_requirement = "None"
		doc.allow_weight_mismatch = 1
		for fieldname, value in values.items():
			doc.set(fieldname, value)
		doc.insert(ignore_permissions=True)
		return doc

	def test_solid_single_mould_route(self):
		operations = [row[0] for row in build_eb_route(self.get_generator().as_dict())]
		self.assertEqual(operations, ["Single Mould Extrusion", "QC After Extrusion", "Packing"])

	def test_solid_wide_sheet_route(self):
		doc = self.get_generator(extrusion_type="Wide Sheet Extrusion")
		operations = [row[0] for row in build_eb_route(doc.as_dict())]
		self.assertIn("Wide Sheet Extrusion", operations)
		self.assertIn("400 mm Slitting", operations)

	def test_online_high_gloss_has_no_separate_online_printing_workstation(self):
		doc = self.get_generator(printing_type="High Gloss", printing_route="Online")
		operations = build_eb_route(doc.as_dict())
		self.assertIn("Single Mould Extrusion + Integrated Online Printing", [row[0] for row in operations])
		self.assertNotIn("Online Printing", [row[1] for row in operations])

	def test_offline_wood_grain_adds_offline_printing_and_qc(self):
		doc = self.get_generator(printing_type="Wood Grain", printing_route="Offline")
		operations = [row[0] for row in build_eb_route(doc.as_dict())]
		self.assertIn("Offline Printing", operations)
		self.assertIn("QC After Offline Printing", operations)

	def test_offline_high_gloss_wide_sheet_80_mm_route(self):
		doc = self.get_generator(
			custom_product_type_design_category="Solid",
			printing_type="High Gloss",
			printing_route="Offline",
			extrusion_type="Wide Sheet Extrusion",
			slitting_requirement="Offline 80 mm Slitting",
		)
		operations = build_eb_route(doc.as_dict())

		self.assertEqual(
			operations,
			[
				("Wide Sheet Extrusion", "Wide Sheet Extrusion"),
				("QC After Extrusion", "QC After Extrusion"),
				("Offline 80 mm Slitting", "80 mm Small Slitting Machine"),
				("Offline Printing", "Offline Printing Machine"),
				("QC After Offline Printing", "QC After Offline Printing"),
				("Packing", "Packing"),
			],
		)

	def test_material_weight_match(self):
		preview = calculate_bom_preview(self.get_generator().as_dict())
		self.assertEqual(preview["material_weight_status"], "Material Weight Matched")

	def test_material_weight_match_normalizes_gram_uom(self):
		preview = calculate_bom_preview(
			self.get_generator(base_colour="TEST-SHADE-GRAM", shade_consumption_per_meter=6).as_dict()
		)
		self.assertEqual(preview["material_weight_status"], "Material Weight Matched")

	def test_material_weight_mismatch_warning(self):
		preview = calculate_bom_preview(
			self.get_generator(shade_consumption_per_meter=0.001).as_dict()
		)
		self.assertEqual(preview["material_weight_status"], "Material Weight Mismatch")
		self.assertIn("Difference", preview["warnings"])

	def test_stock_shortage_warning(self):
		preview = calculate_bom_preview(self.get_generator(source_warehouse=None).as_dict())
		self.assertIn("stock shortage", preview["warnings"].lower())

	def test_missing_workstation_warning(self):
		operation = "TEST Missing Workstation Operation"
		if not frappe.db.exists("Operation", operation):
			op = frappe.new_doc("Operation")
			op.name = operation
			op.insert(ignore_permissions=True)

		if not frappe.db.exists("SmartEdge Process Route", "TEST Missing Workstation Route"):
			route = frappe.new_doc("SmartEdge Process Route")
			route.route_name = "TEST Missing Workstation Route"
			route.enabled = 1
			route.printing_type = "Solid"
			route.printing_route = "None"
			route.extrusion_type = "Single Mould Extrusion"
			route.slitting_type = "Online 80 mm Slitting"
			route.append("operations", {"sequence": 1, "operation": operation, "mandatory": 1})
			route.insert(ignore_permissions=True)

		doc = self.get_generator(slitting_requirement="Online 80 mm Slitting")
		preview = calculate_bom_preview(doc.as_dict())
		self.assertIn("No Workstation configured", preview["warnings"])
