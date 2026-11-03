"""Run against a configured site; every test rolls back its own records."""

import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

from smart_edge_custom.bom import get_bom_batch_details, get_bom_items
from smart_edge_custom.manufacturing.batch_manufacturing import make_batch_work_order


class TestBOMItemGroups(unittest.TestCase):
	def setUp(self):
		self.savepoint = "bom_group_" + uuid4().hex
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.company = frappe.db.get_value("Company", {}, "name")
		self.items = {}
		for group in ("Compounds", "Base Colour", "Additives", "Pigments", "Finished Goods"):
			if not frappe.db.exists("Item Group", group):
				frappe.get_doc({"doctype": "Item Group", "item_group_name": group, "parent_item_group": "All Item Groups"}).insert()
			item = frappe.get_doc({
				"doctype": "Item",
				"item_code": "BOM-GROUP-" + uuid4().hex[:12],
				"item_name": "BOM group test " + group,
				"item_group": group,
				"stock_uom": "Kg",
				"is_stock_item": 1,
				"is_sales_item": 0,
				"include_item_in_manufacturing": 1,
				"valuation_rate": 2,
				"has_batch_no": int(group in ("Compounds", "Base Colour")),
				"create_new_batch": int(group in ("Compounds", "Base Colour")),
			}).insert(ignore_permissions=True, ignore_mandatory=True)
			self.items[group] = item.name

	def make_bom(self, parent, component):
		return frappe.get_doc({
			"doctype": "BOM", "company": self.company,
			"item": self.items[parent], "quantity": 10, "custom_batch": 1,
			"with_operations": 0, "rm_cost_as_per": "Valuation Rate",
			"items": [{"item_code": self.items[component], "qty": 3, "rate": 2}],
		})

	def query(self, parent):
		return get_bom_items("Item", "", "name", 0, 10000, {"parent_item": self.items[parent]})

	def test_compound_and_base_colour_save_submit_without_masters(self):
		for parent, component in (("Compounds", "Additives"), ("Base Colour", "Pigments")):
			with self.subTest(parent=parent):
				bom = self.make_bom(parent, component).insert()
				self.assertEqual(bom.quantity, 3)
				self.assertEqual(bom.items[0].qty, 3)
				self.assertEqual(bom.items[0].uom, "Kg")
				self.assertGreater(bom.total_cost, 0)
				bom.submit()
				self.assertEqual(bom.docstatus, 1)
				work_order = make_batch_work_order(bom.name, bom.item, 2, company=self.company)
				self.assertEqual(work_order.qty, 6)
				self.assertEqual(work_order.custom_weight_per_batch, 3)

	def test_search_restricts_exact_group(self):
		for parent, component in (("Compounds", "Additives"), ("Base Colour", "Pigments")):
			rows = self.query(parent)
			self.assertIn(self.items[component], [row[0] for row in rows])
			self.assertTrue(all(row[2] == component for row in rows))

	def test_invalid_direct_insert_and_submit(self):
		for parent, valid, invalid in (
			("Compounds", "Additives", "Pigments"),
			("Base Colour", "Pigments", "Additives"),
		):
			with self.subTest(parent=parent):
				with self.assertRaisesRegex(frappe.ValidationError, "Row 1"):
					self.make_bom(parent, invalid).insert()
				bom = self.make_bom(parent, valid).insert()
				bom.items[0].item_code = self.items[invalid]
				with self.assertRaisesRegex(frappe.ValidationError, "Row 1"):
					bom.submit()

	def test_parent_change_keeps_rows_and_rejects_invalid_components(self):
		bom = self.make_bom("Compounds", "Additives").insert()
		bom.item = self.items["Base Colour"]
		with self.assertRaisesRegex(frappe.ValidationError, "Row 1"):
			bom.save()
		self.assertEqual(len(bom.items), 1)
		self.assertEqual(bom.items[0].item_code, self.items["Additives"])

	def test_finished_product_remains_unrestricted(self):
		bom = self.make_bom("Finished Goods", "Compounds")
		bom.append("items", {"item_code": self.items["Pigments"], "qty": 2, "rate": 2})
		bom.insert().submit()
		self.assertEqual(bom.docstatus, 1)
		self.assertEqual(bom.quantity, 10)
		self.assertEqual(len(bom.items), 2)
		codes = {row[0] for row in self.query("Finished Goods")}
		self.assertTrue(set(self.items.values()).issubset(codes))

	def test_query_does_not_expand_group_hierarchy(self):
		with patch("erpnext.manufacturing.doctype.bom.bom.item_query", return_value=[]) as query:
			self.query("Compounds")
			self.assertEqual(query.call_args.args[-1]["item_group"], "Additives")

	def test_batch_details_do_not_read_recipe_masters(self):
		with patch("smart_edge_custom.bom.get_item_batch_details", side_effect=AssertionError("Legacy master lookup")):
			for parent in ("Compounds", "Base Colour"):
				details = get_bom_batch_details(self.items[parent])
				self.assertTrue(details["is_batch_manufacturing_item"])
				self.assertEqual(details["output_qty"], 0)

	def test_retiring_process_preserves_historical_values(self):
		from smart_edge_custom.patches.retire_bom_process_field import execute

		bom = self.make_bom("Compounds", "Additives")
		bom.custom_process_2 = "Extrusion"
		bom.insert().submit()
		modified = bom.modified
		execute()
		execute()  # The metadata migration can safely be repeated.
		field = frappe.db.get_value("Custom Field", {"dt": "BOM", "fieldname": "custom_process_2"}, ["hidden", "reqd"], as_dict=True)
		self.assertEqual(field.hidden, 1)
		self.assertEqual(field.reqd, 0)
		bom.reload()
		self.assertEqual(bom.custom_process_2, "Extrusion")
		self.assertEqual(bom.docstatus, 1)
		self.assertEqual(str(bom.modified), str(modified))

	def make_component(self, group, stock_uom="Kg"):
		item = frappe.copy_doc(frappe.get_doc("Item", self.items[group]))
		item.item_code = "BOM-QTY-" + uuid4().hex[:12]
		item.stock_uom = stock_uom
		item.set("uoms", [{"uom": stock_uom, "conversion_factor": 1}])
		return item.insert(ignore_permissions=True, ignore_mandatory=True)

	def test_quantity_add_edit_remove_save_reopen(self):
		for parent, component in (("Compounds", "Additives"), ("Base Colour", "Pigments")):
			with self.subTest(parent=parent):
				second = self.make_component(component)
				bom = self.make_bom(parent, component)
				bom.items[0].qty = 20
				bom.insert()
				self.assertEqual(bom.quantity, 20)
				bom.append("items", {"item_code": second.name, "qty": 30, "rate": 2})
				bom.save()
				self.assertEqual(bom.quantity, 50)
				bom.items[1].qty = 40
				bom.save()
				self.assertEqual(bom.quantity, 60)
				bom.remove(bom.items[0])
				bom.save()
				self.assertEqual(bom.quantity, 40)
				bom.reload()
				self.assertEqual(bom.quantity, 40)
				self.assertEqual(bom.items[0].qty, 40)
				bom.submit()
				self.assertEqual(bom.quantity, 40)

	def test_quantity_converts_stock_uom_and_transaction_uom(self):
		for parent, component in (("Compounds", "Additives"), ("Base Colour", "Pigments")):
			with self.subTest(parent=parent):
				grams = self.make_component(component, "Gram")
				bom = self.make_bom(parent, component)
				bom.items[0].qty = 2
				bom.append("items", {"item_code": grams.name, "qty": 250, "uom": "Gram", "rate": 2})
				bom.insert()
				self.assertEqual(bom.quantity, 2.25)
				bom.reload()
				self.assertEqual(bom.quantity, 2.25)
				# ERPNext's existing transaction-to-stock factor is respected.
				bom.items[0].uom = "Gram"
				bom.items[0].conversion_factor = 0.001
				bom.items[0].qty = 125
				bom.save()
				self.assertEqual(bom.quantity, 0.375)

	def test_quantity_precision_and_incompatible_units(self):
		from smart_edge_custom.bom import get_bom_quantity
		from frappe.utils import flt

		grams = self.make_component("Pigments", "Gram")
		total = get_bom_quantity(self.items["Base Colour"], [{"item_code": grams.name, "qty": 123.456789, "uom": "Gram"}])
		self.assertEqual(total, flt(0.123456789, frappe.get_precision("BOM", "quantity")))
		unit_item = self.make_component("Additives", "Nos")
		with self.assertRaisesRegex(frappe.ValidationError, "No UOM conversion"):
			get_bom_quantity(self.items["Compounds"], [{"item_code": unit_item.name, "qty": 2}])
