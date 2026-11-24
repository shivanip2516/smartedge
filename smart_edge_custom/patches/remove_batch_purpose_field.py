"""Remove the obsolete Smart Edge Batch Purpose Custom Field.

This only removes the managed Custom Field metadata.  It deliberately does not
delete Batch documents, stock ledger rows, or any ERPNext-owned column.
"""
import frappe


def execute():
	field_name = "Batch-custom_batch_purpose"
	if frappe.db.exists("Custom Field", field_name):
		frappe.delete_doc("Custom Field", field_name, force=True, ignore_permissions=True)
	frappe.clear_cache(doctype="Batch")
