import frappe

DUPLICATE_ITEM_FIELDS = (
	"Item-base_shade",
	"Item-base_colour",
	"Item-printing_1st_design",
	"Item-printing_2nd_design",
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
