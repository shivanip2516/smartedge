"""Rename the report to a Python-safe name without duplicating it."""

import frappe


def execute():
	old_name = "Scrap & Recovery Analytics"
	new_name = "Scrap and Recovery Analytics"

	if not frappe.db.exists("Report", old_name):
		return

	if frappe.db.exists("Report", new_name):
		# Keep the legacy URL working as a thin alias to the Python-safe report.
		# This avoids a second implementation while allowing bookmarked old URLs
		# to resolve the valid reference DocType and script module.
		legacy = frappe.get_doc("Report", old_name)
		legacy.report_type = "Custom Report"
		legacy.report_name = new_name
		legacy.ref_doctype = frappe.db.get_value("Report", new_name, "ref_doctype")
		legacy.module = frappe.db.get_value("Report", new_name, "module")
		legacy.disabled = 0
		legacy.save(ignore_permissions=True)
		return

	frappe.rename_doc("Report", old_name, new_name, force=True)
