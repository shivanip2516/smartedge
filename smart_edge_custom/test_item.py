import frappe
from frappe.tests.utils import FrappeTestCase

OPTIONAL_ITEM_GROUPS = ("Additives", "Compounds", "Pigments", "Base Colour")


class TestItemValidation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._ensure_item_groups()
		cls._ensure_finished_product_masters()

	@classmethod
	def _ensure_item_groups(cls):
		for item_group in OPTIONAL_ITEM_GROUPS:
			if frappe.db.exists("Item Group", item_group):
				continue
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": item_group,
					"parent_item_group": "All Item Groups",
				}
			).insert(ignore_permissions=True)

	@classmethod
	def _ensure_finished_product_masters(cls):
		if not frappe.db.exists("Size Weight", "TEST-ITEM-SIZE"):
			frappe.get_doc(
				{
					"doctype": "Size Weight",
					"size_name": "TEST-ITEM-SIZE",
					"width": 22,
					"thickness": 0.4,
					"factor": 1,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Color", "TEST-ITEM-COLOR"):
			frappe.get_doc(
				{
					"doctype": "Color",
					"name": "TEST-ITEM-COLOR",
					"color": "#123456",
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("PVC Type Finish", "TEST-ITEM-FINISH"):
			frappe.get_doc(
				{
					"doctype": "PVC Type Finish",
					"type_finish": "TEST-ITEM-FINISH",
					"abbreviation": "TST",
				}
			).insert(ignore_permissions=True)

	def test_standard_erpnext_item_does_not_require_smartedge_fields(self):
		item = self._new_item("TEST-STANDARD-ERPNext-ITEM", "Services")
		item.insert(ignore_permissions=True)
		self.assertTrue(item.name)

	def test_smartedge_finished_product_requires_dynamic_fields(self):
		item = self._new_item("TEST-SMARTEDGE-INCOMPLETE", "Products")

		with self.assertRaisesRegex(
			frappe.ValidationError,
			"Size, Type Short Code, Color, HSN/SAC",
		):
			item.insert(ignore_permissions=True)

	def test_material_item_groups_are_exempt(self):
		for item_group in OPTIONAL_ITEM_GROUPS:
			item = self._new_item(f"TEST-{item_group.upper().replace(' ', '-')}-ITEM", item_group)
			item.insert(ignore_permissions=True)
			self.assertTrue(item.name)

	def test_valid_smartedge_finished_product_saves(self):
		item = self._new_item("TEST-SMARTEDGE-VALID", "Products")
		item.custom_size = "TEST-ITEM-SIZE"
		item.custom_color = "TEST-ITEM-COLOR"
		item.custom_type_finish = "TEST-ITEM-FINISH"
		item.gst_hsn_code = "39169090"
		item.insert(ignore_permissions=True)

		self.assertEqual(item.custom_type_short_code, "TST")

	@staticmethod
	def _new_item(item_code, item_group):
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": item_group,
				"is_sales_item": 0,
				"stock_uom": "Nos",
			}
		)
