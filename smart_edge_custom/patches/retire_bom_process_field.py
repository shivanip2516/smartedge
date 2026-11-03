import frappe


def execute():
	# Keep the field and its database column so historical Process values survive.
	field = frappe.db.get_value("Custom Field", {"dt": "BOM", "fieldname": "custom_process_2"})
	if field:
		frappe.db.set_value(
			"Custom Field", field,
			{"hidden": 1, "read_only": 1, "print_hide": 1, "reqd": 0},
			update_modified=False,
		)
		frappe.clear_cache(doctype="BOM")
