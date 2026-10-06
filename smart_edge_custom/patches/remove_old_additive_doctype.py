import frappe


def execute():
	if not frappe.db.exists("DocType", "Additive"):
		return

	frappe.db.delete("Additive")
	frappe.delete_doc("DocType", "Additive", force=True, ignore_permissions=True)
	frappe.clear_cache(doctype="Additive")
