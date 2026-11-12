import frappe
import unittest
from frappe.tests.utils import FrappeTestCase

from smart_edge_custom.manufacturing.final_bom_generator import (
	apply_item_configuration_defaults,
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
		cls.items = {}
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

		if not frappe.db.exists("Size Weight", "2x24"):
			frappe.get_doc({"doctype": "Size Weight", "size_name": "2x24", "thickness": 2, "width": 24, "factor": 1.5}).insert()

		cls._ensure_item("TEST-FINAL-EB", "Finished Goods", "Meter", custom_size="2x24")
		if not frappe.db.exists("Material Type", "Solid"):
			frappe.get_doc({"doctype": "Material Type", "material_type_name": "Solid"}).insert()
		if not frappe.db.exists("PVC Type Finish", "TEST-GENERATOR-HIGH-GLOSS"):
			frappe.get_doc({"doctype": "PVC Type Finish", "type_finish": "TEST-GENERATOR-HIGH-GLOSS", "abbreviation": "HG"}).insert()
		cls._ensure_item("TEST-FINAL-EB-HG", "Finished Goods", "Meter", custom_size="2x24", custom_type_finish="TEST-GENERATOR-HIGH-GLOSS", custom_product_type_design_category="Solid")
		cls._ensure_item("TEST-COMPOUND", "Compounds", "Kg")
		cls._ensure_item("TEST-SHADE", "Base Colour", "Kg")
		cls._ensure_item("TEST-SHADE-GRAM", "Base Colour", "Gram")
		cls._ensure_item("TEST-ADDITIVE", "Additives", "Kg")
		cls._ensure_item("TEST-PIGMENT", "Pigments", "Kg")

		for parent, component in (
			("TEST-COMPOUND", "TEST-ADDITIVE"),
			("TEST-SHADE", "TEST-PIGMENT"),
			("TEST-SHADE-GRAM", "TEST-PIGMENT"),
		):
			parent, component = cls.items[parent], cls.items[component]
			if not frappe.db.exists("BOM", {"item": parent, "docstatus": 1, "is_active": 1}):
				frappe.get_doc({
					"doctype": "BOM", "item": parent, "company": cls.company,
					"quantity": 1, "custom_batch": 1,
					"items": [{"item_code": component, "qty": 1, "rate": 1}],
				}).insert().submit()

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
		existing = frappe.db.get_value("Item", {"item_name": item_code}, "name")
		if existing:
			cls.items[item_code] = existing
			return

		doc = frappe.new_doc("Item")
		doc.item_code = item_code
		doc.item_name = item_code
		doc.item_group = item_group
		doc.stock_uom = stock_uom
		doc.include_item_in_manufacturing = 1
		doc.is_sales_item = 0
		for fieldname, value in values.items():
			if doc.meta.has_field(fieldname):
				doc.set(fieldname, value)
		doc.insert(ignore_permissions=True)
		cls.items[item_code] = doc.name

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
		doc.source_warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")
		doc.finished_item = self.items["TEST-FINAL-EB"]
		doc.production_quantity = 1000
		doc.thickness_mm = 2
		doc.width_mm = 24
		doc.weight_factor = 1.5
		doc.compound_item = self.items["TEST-COMPOUND"]
		doc.compound_consumption_batches = 66
		doc.base_colour_item = self.items["TEST-SHADE"]
		doc.shade_consumption_batches = 6
		doc.printing_type = "Solid"
		doc.printing_route = "None"
		doc.extrusion_type = "Single Mould Extrusion"
		doc.slitting_requirement = "None"
		doc.allow_weight_mismatch = 1
		for fieldname, value in values.items():
			if fieldname in ("finished_item", "compound_item", "base_colour_item"):
				value = self.items.get(value, value)
			doc.set(fieldname, value)
		doc.insert(ignore_permissions=True)
		return doc

	def test_solid_single_mould_route(self):
		operations = [row[0] for row in build_eb_route(self.get_generator().as_dict())]
		self.assertEqual(operations, ["Single Mould Extrusion", "QC After Extrusion", "Packing"])

	def test_weight_factor_uses_size_weight_master(self):
		doc = self.get_generator()
		doc.weight_factor = 99
		apply_item_configuration_defaults(doc)
		self.assertEqual(doc.weight_factor, frappe.db.get_value("Size Weight", "2x24", "factor"))

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

	def test_printing_selection_drives_preview_save_and_planning_without_finished_bom(self):
		from unittest.mock import patch
		from smart_edge_custom.manufacturing.final_bom_generator import send_generator_to_planning

		for printing_route in ("Online", "Offline"):
			with self.subTest(printing_route=printing_route):
				doc = self.get_generator(finished_item="TEST-FINAL-EB-HG", printing_route=printing_route)
				self.assertFalse(frappe.db.exists("BOM", {"item": doc.finished_item}))
				expected = build_eb_route(doc.as_dict())
				self.assertTrue(expected)
				self.assertEqual([(r.operation, r.workstation) for r in doc.operation_preview], expected)
				doc.save()
				doc.reload()
				self.assertEqual(doc.printing_route, printing_route)
				self.assertEqual([(r.operation, r.workstation) for r in doc.operation_preview], expected)
				counts = {dt: frappe.db.count(dt) for dt in ("Batch", "Stock Entry", "Work Order", "SmartEdge Production Lot")}
				# Isolate routing/planning from physical raw-material stock requirements.
				with patch("smart_edge_custom.manufacturing.final_bom_generator.validate_material_availability_for_release"):
					first = send_generator_to_planning(doc)
					second = send_generator_to_planning(doc)
				self.assertEqual(first["entries"], second["entries"])
				entries = [frappe.get_doc("SmartEdge Production Plan Entry", name) for name in first["entries"]]
				self.assertEqual([(r.operation, r.workstation) for r in entries], expected)
				self.assertEqual([r.sequence for r in entries], [r.sequence for r in doc.operation_preview])
				self.assertEqual(counts, {dt: frappe.db.count(dt) for dt in counts})

	def test_offline_wood_grain_adds_offline_printing_and_qc(self):
		doc = self.get_generator(printing_type="Wood Grain", printing_route="Offline")
		operations = [row[0] for row in build_eb_route(doc.as_dict())]
		self.assertIn("Offline Printing", operations)
		self.assertIn("QC After Offline Printing", operations)

	def test_offline_high_gloss_wide_sheet_80_mm_route(self):
		doc = self.get_generator(
			finished_item="TEST-FINAL-EB-HG",
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
			self.get_generator(base_colour_item="TEST-SHADE-GRAM", shade_consumption_batches=6).as_dict()
		)
		self.assertEqual(preview["material_weight_status"], "Material Weight Matched")

	def test_batch_based_weight_calculation(self):
		preview = calculate_bom_preview(
			self.get_generator(compound_consumption_batches=3, shade_consumption_batches=2).as_dict()
		)
		self.assertEqual(preview["compound_total_required"], 3)
		self.assertEqual(preview["shade_total_required"], 2)
		self.assertEqual(preview["total_eb_weight_kg"], 5)
		self.assertAlmostEqual(preview["weight_per_meter_kg"], 0.072, places=6)
		self.assertAlmostEqual(preview["production_quantity"], 69.444444, places=6)
		self.assertAlmostEqual(preview["compound_consumption_per_meter"], 0.0432, places=6)
		self.assertAlmostEqual(preview["shade_consumption_per_meter"], 0.0288, places=6)

	def test_batch_based_production_quantity_uses_size_weight(self):
		preview = calculate_bom_preview(self.get_generator().as_dict())
		self.assertEqual(preview["total_eb_weight_kg"], 72)
		self.assertAlmostEqual(preview["weight_per_meter_kg"], 0.072, places=6)
		self.assertAlmostEqual(preview["production_quantity"], 1000, places=6)

	def test_stock_shortage_warning(self):
		preview = calculate_bom_preview(self.get_generator(source_warehouse=None).as_dict())
		self.assertIn("stock shortage", preview["warnings"].lower())

	def test_missing_workstation_warning(self):
		from unittest.mock import patch

		doc = self.get_generator()
		from smart_edge_custom.manufacturing.final_bom_generator import resolve_process_route
		route, _ = resolve_process_route(doc)
		route.operations[0].workstation = None
		with patch("smart_edge_custom.manufacturing.final_bom_generator.resolve_process_route", return_value=(route, None)):
			preview = calculate_bom_preview(doc.as_dict())
		self.assertIn("No Workstation configured", preview["warnings"])
