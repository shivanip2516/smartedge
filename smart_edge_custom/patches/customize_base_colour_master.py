import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "Base Colour"):
		return

	ensure_weightage_field()
	apply_property_setters()
	frappe.clear_cache(doctype="Base Colour")


def ensure_weightage_field():
	create_custom_fields(
		{
			"Base Colour": [
				{
					"fieldname": "weightage",
					"fieldtype": "Int",
					"label": "Weightage",
					"insert_after": "colour_name",
					"reqd": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"non_negative": 1,
					"description": "Lower = lighter (processed first). Higher = darker.",
					"module": "smart_edge_custom",
				}
			]
		},
		update=True,
	)


def apply_property_setters():
	for property_name, value in {
		"search_fields": "colour_name",
		"sort_field": "weightage",
		"sort_order": "ASC",
	}.items():
		frappe.make_property_setter(
			{
				"doctype": "Base Colour",
				"doctype_or_field": "DocType",
				"property": property_name,
				"value": value,
				"property_type": "Data",
			}
		)
		property_setter = frappe.db.get_value(
			"Property Setter",
			{
				"doc_type": "Base Colour",
				"doctype_or_field": "DocType",
				"property": property_name,
			},
		)
		if property_setter:
			frappe.db.set_value("Property Setter", property_setter, "module", "smart_edge_custom")
