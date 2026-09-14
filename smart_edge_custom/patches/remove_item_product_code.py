import frappe
from frappe import _


def execute():
	if not frappe.db.has_column("Item", "product_code"):
		delete_custom_field_record()
		return

	product_code_count = frappe.db.sql(
		"""
		select count(*)
		from `tabItem`
		where ifnull(product_code, '') != ''
		""",
	)[0][0]

	if product_code_count:
		frappe.throw(
			_(
				"Cannot remove Item Product Code because {0} Item records still have values. "
				"Review whether these values should be copied to Customer Order Code first."
			).format(product_code_count)
		)

	delete_custom_field_record()
	frappe.clear_cache(doctype="Item")


def delete_custom_field_record():
	if frappe.db.exists("Custom Field", "Item-product_code"):
		frappe.delete_doc(
			"Custom Field",
			"Item-product_code",
			ignore_permissions=True,
			force=True,
		)
