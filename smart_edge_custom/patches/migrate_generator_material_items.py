"""Migrate explicit master-to-Item mappings before retiring Generator Link fields.

The legacy database columns are deliberately retained for rollback/audit.
"""

import frappe


GENERATOR = "Final EB BOM Generator"
MAPPINGS = (
	("compound", "compound_item", "Compound", "Compounds", ("item", "item_code", "compound_item", "compound_name")),
	("base_colour", "base_colour_item", "Base Colour", "Base Colour", ("item", "item_code", "base_colour_item", "colour_item", "colour_name")),
)


def resolve_explicit_item(doctype, name, group, fields):
	if not frappe.db.exists(doctype, name):
		return None, "legacy master does not exist"
	master = frappe.get_doc(doctype, name)
	candidates = {
		master.get(field) for field in fields
		if master.meta.has_field(field) and master.get(field)
		and frappe.db.get_value("Item", master.get(field), "item_group") == group
	}
	if len(candidates) == 1:
		return candidates.pop(), None
	return None, "ambiguous explicit Item mappings" if candidates else "no explicit Item mapping in the required Item Group"


def execute():
	if not frappe.db.exists("DocType", GENERATOR):
		return
	migrated = 0
	unresolved = []
	for legacy, target, doctype, group, fields in MAPPINGS:
		if not frappe.db.has_column(GENERATOR, legacy):
			continue
		# Column identifiers are fixed constants above, never user input.
		rows = frappe.db.sql(
			f"select name, `{legacy}` as legacy_value from `tabFinal EB BOM Generator` "
			f"where coalesce(`{target}`, '')='' and coalesce(`{legacy}`, '')!='' for update",
			as_dict=True,
		)
		for row in rows:
			item, reason = resolve_explicit_item(doctype, row.legacy_value, group, fields)
			if not item:
				unresolved.append(f"{row.name}: {legacy}={row.legacy_value}: {reason}")
				continue
			frappe.db.set_value(GENERATOR, row.name, target, item, update_modified=False)
			migrated += 1
	if unresolved:
		# A failed pre-model-sync patch stops schema retirement and is rolled back.
		frappe.throw("Generator Item migration needs review before legacy fields can be retired:<br>" + "<br>".join(unresolved))
	print(f"Generator Item migration: {migrated} empty Item links populated; legacy columns and all manufacturing references retained.")
