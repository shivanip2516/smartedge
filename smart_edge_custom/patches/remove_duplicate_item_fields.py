import frappe

DUPLICATE_ITEM_FIELDS = (
	"Item-item_category",
	"Item-material_type",
	"Item-color",
)


def execute():
	for custom_field in DUPLICATE_ITEM_FIELDS:
		if frappe.db.exists("Custom Field", custom_field):
			frappe.delete_doc(
				"Custom Field",
				custom_field,
				ignore_permissions=True,
				force=True,
			)

	frappe.clear_cache(doctype="Item")
