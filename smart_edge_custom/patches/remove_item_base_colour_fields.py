"""Retire the obsolete Base Colour links from Item safely and idempotently."""

import json

import frappe


FIELDNAMES = ("custom_base_color", "custom_base_colour")


def execute():
	# Preserve any historical values before the Custom Field definitions are
	# removed.  This file is an audit backup only; manufacturing no longer reads
	# either field and no Item records are modified by this patch.
	existing = [fieldname for fieldname in FIELDNAMES if frappe.db.has_column("Item", fieldname)]
	if existing:
		rows = frappe.get_all("Item", fields=["name", *existing], limit_page_length=0)
		if rows:
			path = frappe.get_site_path("private", "files", "retired_item_base_colour_fields.json")
			with open(path, "w", encoding="utf-8") as handle:
				json.dump({"fields": existing, "items": rows}, handle, default=str, indent=2)

	for fieldname in FIELDNAMES:
		custom_field = f"Item-{fieldname}"
		if frappe.db.exists("Custom Field", custom_field):
			frappe.delete_doc("Custom Field", custom_field, force=True, ignore_permissions=True)

	frappe.clear_cache(doctype="Item")
