"""Transactional integration coverage for the Generator's Item-based material links."""

import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import frappe

from smart_edge_custom.manufacturing.batch_manufacturing import get_active_batch_bom, make_batch_work_order
from smart_edge_custom.manufacturing.final_bom_generator import (
	calculate_bom_preview,
	get_prepared_component_batch_weight,
	send_generator_to_planning,
)
from smart_edge_custom.manufacturing.material_production import build_material_planning_context
from smart_edge_custom.patches.migrate_generator_material_items import execute, resolve_explicit_item
from smart_edge_custom import test_bom_item_groups as bom_tests


class TestGeneratorItemLinks(unittest.TestCase):
	make_bom = bom_tests.TestBOMItemGroups.make_bom
	make_component = bom_tests.TestBOMItemGroups.make_component

	def setUp(self):
		bom_tests.TestBOMItemGroups.setUp(self)
		self.warehouse = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 0}, "name")
		self.boms = {}
		for parent, component in (("Compounds", "Additives"), ("Base Colour", "Pigments")):
			bom = self.make_bom(parent, component).insert().submit()
			self.boms[parent] = bom.name
		self.finished_bom = self.make_finished_bom()

	def make_finished_bom(self):
		workstation = frappe.db.get_value("Workstation", {"disabled": 0}, "name")
		return frappe.get_doc({"doctype": "BOM", "item": self.items["Finished Goods"], "company": self.company,
			"quantity": 1, "with_operations": 1,
			"items": [{"item_code": self.items["Compounds"], "qty": 1, "rate": 1}],
			"operations": [{"operation": operation, "workstation": workstation, "time_in_mins": 1, "sequence_id": index}
				for index, operation in enumerate(("Extrusion", "Printing", "Packing"), 1)],
		}).insert().submit().name

	def source(self):
		return {
			"finished_item": self.items["Finished Goods"], "company": self.company,
			"compound_item": self.items["Compounds"], "base_colour_item": self.items["Base Colour"],
			"compound_consumption_batches": 2, "shade_consumption_batches": 3,
			"source_warehouse": self.warehouse, "thickness_mm": 2, "width_mm": 24, "weight_factor": 1.5,
			"printing_type": "Solid", "printing_route": "None", "extrusion_type": "Single Mould Extrusion",
			"slitting_requirement": "None", "allow_weight_mismatch": 1,
		}

	def generator(self):
		return frappe.get_doc({"doctype": "Final EB BOM Generator", **self.source()}).insert()

	def test_schema_and_authoritative_links(self):
		meta = frappe.get_meta("Final EB BOM Generator")
		self.assertFalse(meta.has_field("compound"))
		self.assertFalse(meta.has_field("base_colour"))
		for name in ("compound_item", "base_colour_item"):
			field = meta.get_field(name)
			self.assertEqual((field.fieldtype, field.options, field.read_only), ("Link", "Item", 0))
		doc = self.generator()
		doc.reload()
		self.assertEqual(doc.compound_item, self.items["Compounds"])
		self.assertEqual(doc.base_colour_item, self.items["Base Colour"])

	def test_preview_planning_and_work_order_use_items_without_masters(self):
		original_get_value, original_get_doc = frappe.db.get_value, frappe.get_doc
		def get_value(doctype, *args, **kwargs):
			self.assertNotIn(doctype, ("Compound", "Base Colour", "Compound Additive", "Base Colour Pigment"))
			return original_get_value(doctype, *args, **kwargs)
		def get_doc(doctype, *args, **kwargs):
			if isinstance(doctype, str):
				self.assertNotIn(doctype, ("Compound", "Base Colour"))
			return original_get_doc(doctype, *args, **kwargs)
		with patch.object(frappe.db, "get_value", side_effect=get_value), patch.object(frappe, "get_doc", side_effect=get_doc):
			preview = calculate_bom_preview({**self.source(), "compound": "OBSOLETE", "base_colour": "OBSOLETE"})
			self.assertEqual(preview["compound_total_required"], 6)
			self.assertEqual(preview["shade_total_required"], 9)
			self.assertEqual(preview["total_eb_weight_kg"], 15)
			self.assertAlmostEqual(preview["production_quantity"], 15 / 0.072)
			self.assertEqual(preview["material_weight_status"], "Material Weight Matched")
			doc = self.generator()
			context = build_material_planning_context(doc)
			self.assertEqual([row["item_code"] for row in context["requirements"]], [doc.compound_item, doc.base_colour_item])
			for row in context["requirements"]:
				work_order = make_batch_work_order(row["bom_no"], row["item_code"], 2, company=self.company)
				self.assertEqual(work_order.qty, 6)
				self.assertEqual(work_order.required_items[0].required_qty, 6)

	def test_server_group_and_missing_bom_validation(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Item Group Compounds"):
			calculate_bom_preview({**self.source(), "compound_item": self.items["Pigments"]})
		unconfigured = self.make_component("Additives")
		with self.assertRaisesRegex(frappe.ValidationError, "active submitted one-batch BOM is required"):
			get_prepared_component_batch_weight(unconfigured.name, "Test material")

	def test_bom_default_priority_and_invalid_bom_exclusion(self):
		other = self.make_bom("Compounds", "Additives")
		other.items[0].qty = 5
		other.is_default = 0
		other.insert().submit()
		self.assertEqual(get_active_batch_bom(self.items["Compounds"]), self.boms["Compounds"])
		self.assertEqual(get_prepared_component_batch_weight(self.items["Compounds"], "Compound"), 3)
		frappe.db.set_value("BOM", self.boms["Compounds"], "is_default", 0)
		# Explicit timestamps keep the existing modified-date tie-break deterministic.
		frappe.db.set_value("BOM", other.name, "modified", "2099-01-01 00:00:00", update_modified=False)
		self.assertEqual(get_active_batch_bom(self.items["Compounds"]), other.name)
		other.db_set("is_active", 0)
		self.assertEqual(get_active_batch_bom(self.items["Compounds"]), self.boms["Compounds"])

	def test_send_to_planning_does_not_duplicate_entries_or_boms(self):
		doc = self.generator()
		bom_count = frappe.db.count("BOM")
		# Release checks require stock; this test isolates planning idempotency.
		with patch("smart_edge_custom.manufacturing.final_bom_generator.validate_material_availability_for_release"):
			first = send_generator_to_planning(doc)
			second = send_generator_to_planning(doc)
		self.assertFalse(first["already_sent"])
		self.assertTrue(second["already_sent"])
		self.assertEqual(set(first["entries"]), set(second["entries"]))
		self.assertEqual(frappe.db.count("BOM"), bom_count)
		self.assertEqual(frappe.db.count("SmartEdge Production Plan Entry", {"final_eb_bom_generator": doc.name}), len(first["entries"]))

	def legacy_record(self, compound, compound_item=None, base_colour=None):
		name = "MIGRATION-" + uuid4().hex[:12]
		frappe.db.sql(
			"insert into `tabFinal EB BOM Generator` (name, compound, compound_item, base_colour, status) values (%s,%s,%s,%s,'Draft')",
			(name, compound, compound_item, base_colour),
		)
		return name

	def test_historical_mapping_is_idempotent_and_preserves_existing_links(self):
		master = frappe.get_doc({"doctype": "Compound", "compound_name": self.items["Compounds"],
			"additives": [{"additive": self.items["Additives"], "quantity_kg": 3}]}).insert()
		shade = frappe.get_doc({"doctype": "Base Colour", "colour_name": self.items["Base Colour"], "weightage": 5,
			"pigments": [{"pigment_item": self.items["Pigments"], "quantity_kg": 3}]}).insert()
		name = self.legacy_record(master.name, base_colour=shade.name)
		already_set = self.legacy_record("MISSING-LEGACY-MASTER", self.items["Compounds"])
		execute()
		execute()
		self.assertEqual(frappe.db.get_value("Final EB BOM Generator", name, "compound_item"), self.items["Compounds"])
		self.assertEqual(frappe.db.get_value("Final EB BOM Generator", name, "base_colour_item"), self.items["Base Colour"])
		self.assertEqual(frappe.db.sql("select compound from `tabFinal EB BOM Generator` where name=%s", name)[0][0], master.name)
		self.assertEqual(frappe.db.get_value("Final EB BOM Generator", already_set, "compound_item"), self.items["Compounds"])

	def test_unmapped_history_blocks_migration_without_guessing(self):
		name = self.legacy_record(self.items["Compounds"])
		with self.assertRaisesRegex(frappe.ValidationError, "legacy master does not exist"):
			execute()
		self.assertFalse(frappe.db.get_value("Final EB BOM Generator", name, "compound_item"))

	def test_ambiguous_explicit_mappings_are_reported(self):
		second = self.make_component("Additives")
		second.item_group = "Compounds"
		second.save(ignore_permissions=True)
		master = MagicMock()
		master.meta.has_field.return_value = True
		master.get.side_effect = {"compound_name": self.items["Compounds"], "item_code": second.name}.get
		with patch("smart_edge_custom.patches.migrate_generator_material_items.frappe.db.exists", return_value=True), patch("smart_edge_custom.patches.migrate_generator_material_items.frappe.get_doc", return_value=master):
			item, reason = resolve_explicit_item("Compound", "LEGACY", "Compounds", ("compound_name", "item_code"))
		self.assertIsNone(item)
		self.assertIn("ambiguous", reason)
