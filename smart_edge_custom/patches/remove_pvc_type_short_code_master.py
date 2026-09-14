import frappe


TYPE_SHORT_CODE_CUSTOM_FIELD = "Item-custom_type_short_code"
TYPE_SHORT_CODE_FIELD_PROPERTIES = (
	"dt",
	"fieldname",
	"label",
	"insert_after",
	"reqd",
	"description",
	"default",
	"fetch_from",
	"fetch_if_empty",
	"read_only",
	"allow_on_submit",
	"depends_on",
	"mandatory_depends_on",
	"read_only_depends_on",
	"collapsible_depends_on",
	"allow_in_quick_entry",
	"in_global_search",
	"in_list_view",
	"in_standard_filter",
	"in_preview",
	"bold",
	"translatable",
	"print_hide",
	"report_hide",
	"hidden",
	"search_index",
	"unique",
	"no_copy",
	"permlevel",
	"columns",
	"length",
	"module",
)


def execute():
	backfill_type_finish_abbreviations()
	convert_item_type_short_code_to_data()
	delete_type_short_code_doctype()
	frappe.clear_cache(doctype="Item")


def backfill_type_finish_abbreviations():
	backfill_from_type_short_code_master()
	backfill_from_items()


def backfill_from_type_short_code_master():
	if not frappe.db.exists("DocType", "PVC Type Short Code"):
		return

	for row in frappe.get_all(
		"PVC Type Short Code",
		fields=["name", "type_short_code", "type_finish"],
	):
		if not row.type_finish or not row.type_short_code:
			continue

		set_type_finish_abbreviation(row.type_finish, row.type_short_code)


def backfill_from_items():
	if not frappe.db.has_column("Item", "custom_type_finish") or not frappe.db.has_column(
		"Item",
		"custom_type_short_code",
	):
		return

	for row in frappe.get_all(
		"Item",
		filters={
			"custom_type_finish": ["is", "set"],
			"custom_type_short_code": ["is", "set"],
		},
		fields=["custom_type_finish", "custom_type_short_code"],
		group_by="custom_type_finish, custom_type_short_code",
	):
		set_type_finish_abbreviation(row.custom_type_finish, row.custom_type_short_code)


def set_type_finish_abbreviation(type_finish, abbreviation):
	if not frappe.db.exists("PVC Type Finish", type_finish):
		return

	if frappe.db.get_value("PVC Type Finish", type_finish, "abbreviation"):
		return

	frappe.db.set_value("PVC Type Finish", type_finish, "abbreviation", abbreviation)


def convert_item_type_short_code_to_data():
	if not frappe.db.exists("Custom Field", TYPE_SHORT_CODE_CUSTOM_FIELD):
		return

	custom_field = frappe.get_doc("Custom Field", TYPE_SHORT_CODE_CUSTOM_FIELD)
	if custom_field.fieldtype == "Data" and not custom_field.options:
		return

	field_definition = get_type_short_code_field_definition(custom_field)
	existing_values = get_existing_type_short_code_values()

	frappe.delete_doc(
		"Custom Field",
		TYPE_SHORT_CODE_CUSTOM_FIELD,
		ignore_permissions=True,
		force=True,
	)
	frappe.clear_cache(doctype="Item")

	frappe.get_doc(field_definition).insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Item")

	restore_type_short_code_values(existing_values)


def get_type_short_code_field_definition(custom_field):
	field_definition = {
		"doctype": "Custom Field",
		"name": TYPE_SHORT_CODE_CUSTOM_FIELD,
		"fieldtype": "Data",
	}

	for fieldname in TYPE_SHORT_CODE_FIELD_PROPERTIES:
		field_definition[fieldname] = custom_field.get(fieldname)

	return field_definition


def get_existing_type_short_code_values():
	if not frappe.db.has_column("Item", "custom_type_short_code"):
		return []

	return frappe.get_all(
		"Item",
		filters={"custom_type_short_code": ["is", "set"]},
		fields=["name", "custom_type_short_code"],
	)


def restore_type_short_code_values(existing_values):
	if not existing_values:
		return

	for row in existing_values:
		frappe.db.set_value(
			"Item",
			row.name,
			"custom_type_short_code",
			row.custom_type_short_code,
			update_modified=False,
		)


def delete_type_short_code_doctype():
	if not frappe.db.exists("DocType", "PVC Type Short Code"):
		return

	frappe.delete_doc(
		"DocType",
		"PVC Type Short Code",
		ignore_permissions=True,
		force=True,
	)
