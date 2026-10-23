import json

import frappe

from smart_edge_custom.base_colour import calculate_recipe_total


def execute():
	if not frappe.db.exists("DocType", "Base Colour"):
		return

	ambiguous = []
	for master in frappe.get_all("Base Colour", fields=["name", "colour_name", "total_kg"]):
		rows = frappe.get_all(
			"Base Colour Pigment",
			filters={"parent": master.name, "parenttype": "Base Colour"},
			fields=["pigment_item", "uom", "quantity_kg"],
			order_by="idx asc",
		)
		reason = get_ambiguity(master, rows)
		if reason:
			ambiguous.append({"name": master.name, "colour_name": master.colour_name, "reason": reason})
			continue

		try:
			total = calculate_recipe_total(master.colour_name, rows)["total"]
		except (frappe.ValidationError, ValueError, TypeError, ArithmeticError) as error:
			ambiguous.append(
				{"name": master.name, "colour_name": master.colour_name, "reason": str(error)}
			)
			continue
		frappe.db.set_value("Base Colour", master.name, "total_kg", total, update_modified=False)

	if ambiguous:
		path = frappe.get_site_path("private", "files", "ambiguous_base_colour_uom_records.json")
		with open(path, "w", encoding="utf-8") as handle:
			json.dump(ambiguous, handle, indent=2, default=str)
		print(
			"Base Colour totals not migrated because their legacy UOM is ambiguous: {0}. "
			"See private/files/ambiguous_base_colour_uom_records.json".format(
				", ".join(row["name"] for row in ambiguous)
			)
		)


def get_ambiguity(master, rows):
	base_item = frappe.db.get_value(
		"Item", master.colour_name, ["item_group", "stock_uom"], as_dict=True
	)
	if not base_item or not base_item.stock_uom:
		return "Base Colour Item is missing or has no Stock UOM."
	if base_item.item_group != "Base Colour":
		return "Linked Item is not in the Base Colour Item Group."
	if not rows and master.total_kg:
		return "A non-zero legacy total exists without pigment rows."

	for row in rows:
		pigment_item = frappe.db.get_value(
			"Item", row.pigment_item, ["item_group", "stock_uom"], as_dict=True
		)
		if not pigment_item or not pigment_item.stock_uom:
			return "Pigment Item {0} is missing or has no Stock UOM.".format(row.pigment_item)
		if pigment_item.item_group != "Pigments":
			return "Item {0} is not in the Pigments Item Group.".format(row.pigment_item)
		if not row.uom:
			return "Pigment Item {0} has no saved UOM.".format(row.pigment_item)
		if row.uom != pigment_item.stock_uom:
			return "Saved UOM {0} does not match {1} Stock UOM {2}.".format(
				row.uom, row.pigment_item, pigment_item.stock_uom
			)
	return None
