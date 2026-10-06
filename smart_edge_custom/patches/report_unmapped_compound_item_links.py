import json

import frappe


COMPOUND_ITEM_GROUP = "Compounds"
ADDITIVE_ITEM_GROUP = "Additives"


def execute():
	report_unmapped_compounds()
	report_unmapped_compound_additives()
	frappe.clear_cache(doctype="Compound")
	frappe.clear_cache(doctype="Compound Additive")


def report_unmapped_compounds():
	if not frappe.db.exists("DocType", "Compound"):
		return

	unmapped = []
	for row in frappe.get_all("Compound", fields=["name", "compound_name"]):
		item_group = frappe.db.get_value("Item", row.compound_name, "item_group")
		if item_group != COMPOUND_ITEM_GROUP:
			unmapped.append(row)

	if unmapped:
		write_private_json("unmapped_compound_items.json", unmapped)
		print(
			"Compound records without matching Item Group '{0}' Item: {1}".format(
				COMPOUND_ITEM_GROUP,
				", ".join(row.compound_name or row.name for row in unmapped),
			)
		)


def report_unmapped_compound_additives():
	if not frappe.db.exists("DocType", "Compound Additive"):
		return

	unmapped = []
	for row in frappe.get_all("Compound Additive", fields=["parent", "additive"]):
		item_group = frappe.db.get_value("Item", row.additive, "item_group")
		if item_group != ADDITIVE_ITEM_GROUP:
			unmapped.append(row)

	if unmapped:
		write_private_json("unmapped_compound_additive_items.json", unmapped)
		print(
			"Compound additive rows without matching Item Group '{0}' Item: {1}".format(
				ADDITIVE_ITEM_GROUP,
				", ".join("{0}: {1}".format(row.parent, row.additive) for row in unmapped),
			)
		)


def write_private_json(filename, rows):
	path = frappe.get_site_path("private", "files", filename)
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(rows, handle, indent=2, default=str)
