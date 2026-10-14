import frappe


def execute():
	if not frappe.db.exists("DocType", "Base Colour"):
		return
	if not frappe.db.exists("Custom Field", "Item-custom_base_color"):
		return
	frappe.db.set_value(
		"Custom Field",
		"Item-custom_base_color",
		{
			"label": "Base Colour",
			"options": "Base Colour",
			"hidden": 1,
			"reqd": 0,
			"read_only": 1,
			"allow_in_quick_entry": 0,
			"in_global_search": 0,
			"in_list_view": 0,
			"in_standard_filter": 0,
			"module": "smart_edge_custom",
		},
	)
	frappe.clear_cache(doctype="Item")
