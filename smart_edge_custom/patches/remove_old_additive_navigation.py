import frappe


OLD_DOCTYPE = "Additive"


def execute():
	remove_workspace_references()
	remove_exact_metadata_references()
	remove_route_history()
	frappe.clear_cache()
	frappe.clear_cache(doctype=OLD_DOCTYPE)


def remove_workspace_references():
	for doctype in ("Workspace Shortcut", "Workspace Link", "Workspace Sidebar Item", "Desktop Icon"):
		if not frappe.db.exists("DocType", doctype):
			continue

		for row in find_explicit_additive_rows(doctype):
			frappe.delete_doc(doctype, row.name, force=True, ignore_permissions=True)


def find_explicit_additive_rows(doctype):
	meta = frappe.get_meta(doctype)
	fields = [field.fieldname for field in meta.fields]
	filters = []

	if "label" in fields:
		filters.append([doctype, "label", "=", OLD_DOCTYPE])
	if "link_to" in fields:
		filters.append([doctype, "link_to", "=", OLD_DOCTYPE])
	if "url" in fields:
		filters.extend(
			[
				[doctype, "url", "=", "/app/additive"],
				[doctype, "url", "=", "app/additive"],
				[doctype, "url", "=", "#List/Additive/List"],
			]
		)
	if "link" in fields:
		filters.extend(
			[
				[doctype, "link", "=", "/app/additive"],
				[doctype, "link", "=", "app/additive"],
				[doctype, "link", "=", "#List/Additive/List"],
			]
		)

	if not filters:
		return []

	return frappe.get_all(doctype, or_filters=filters, fields=["name"])


def remove_exact_metadata_references():
	if frappe.db.exists("DocType", "Global Search DocType"):
		delete_rows("Global Search DocType", {"document_type": OLD_DOCTYPE})

	if frappe.db.exists("DocType", "Custom DocPerm"):
		delete_rows("Custom DocPerm", {"parent": OLD_DOCTYPE})

	if frappe.db.exists("DocType", "Client Script"):
		delete_rows("Client Script", {"dt": OLD_DOCTYPE})

	if frappe.db.exists("DocType", "Property Setter"):
		delete_rows("Property Setter", {"doc_type": OLD_DOCTYPE})

	if frappe.db.exists("DocType", "DocType Link"):
		for filters in (
			{"parent": OLD_DOCTYPE},
			{"link_doctype": OLD_DOCTYPE},
			{"parent_doctype": OLD_DOCTYPE},
		):
			delete_rows("DocType Link", filters)


def delete_rows(doctype, filters):
	for row in frappe.get_all(doctype, filters=filters, fields=["name"]):
		frappe.delete_doc(doctype, row.name, force=True, ignore_permissions=True)


def remove_route_history():
	if not frappe.db.exists("DocType", "Route History"):
		return

	for row in frappe.get_all(
		"Route History",
		filters={"route": ["like", "%Additive%"]},
		fields=["name", "route"],
	):
		parts = [part.strip() for part in (row.route or "").split("/")]
		if OLD_DOCTYPE in parts:
			frappe.delete_doc("Route History", row.name, force=True, ignore_permissions=True)
