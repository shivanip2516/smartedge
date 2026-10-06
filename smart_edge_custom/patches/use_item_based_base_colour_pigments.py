import json

import frappe


BASE_COLOUR_ITEM_GROUP = "Base Colour"
PIGMENT_ITEM_GROUP = "Pigments"


def execute():
	report_unmatched_base_colours()
	backup_and_remove_old_pigment_doctype()
	frappe.clear_cache(doctype="Base Colour")
	frappe.clear_cache(doctype="Item")


def report_unmatched_base_colours():
	if not frappe.db.exists("DocType", "Base Colour") or not frappe.db.has_column("Base Colour", "colour_name"):
		return

	unmatched = []
	for row in frappe.get_all("Base Colour", fields=["name", "colour_name"]):
		item_group = frappe.db.get_value("Item", row.colour_name, "item_group")
		if item_group != BASE_COLOUR_ITEM_GROUP:
			unmatched.append(row)

	if unmatched:
		write_private_json("unmatched_base_colour_items.json", unmatched)
		print(
			"Base Colour records without matching Item Group '{0}' Item: {1}".format(
				BASE_COLOUR_ITEM_GROUP,
				", ".join(row.colour_name or row.name for row in unmatched),
			)
		)


def backup_and_remove_old_pigment_doctype():
	if not frappe.db.exists("DocType", "Pigment"):
		return

	records = frappe.get_all(
		"Pigment",
		fields=["name", "pigment_code", "pigment_name", "supplier"],
		order_by="name asc",
	)
	if records:
		write_private_json("old_pigment_records_before_doctype_removal.json", records)
		print(
			"Old Pigment records backed up before removing Pigment DocType: {0}".format(
				", ".join(row.name for row in records)
			)
		)

	frappe.delete_doc("DocType", "Pigment", ignore_permissions=True, force=True)


def write_private_json(filename, rows):
	path = frappe.get_site_path("private", "files", filename)
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(rows, handle, indent=2, default=str)
