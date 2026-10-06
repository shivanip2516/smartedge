import frappe


ITEM_CUSTOM_FIELDS = (
	"Item-custom_size",
	"Item-custom_color",
	"Item-custom_type_short_code",
)


def execute():
	for custom_field in ITEM_CUSTOM_FIELDS:
		if frappe.db.exists("Custom Field", custom_field):
			frappe.db.set_value("Custom Field", custom_field, "reqd", 0)

	if frappe.get_meta("Item").has_field("gst_hsn_code"):
		ensure_hsn_mandatory_depends_on_property_setter()

	frappe.clear_cache(doctype="Item")


def ensure_hsn_mandatory_depends_on_property_setter():
	property_setter_name = "Item-gst_hsn_code-mandatory_depends_on"
	values = {
		"doctype_or_field": "DocField",
		"doc_type": "Item",
		"field_name": "gst_hsn_code",
		"property": "mandatory_depends_on",
		"value": "",
		"property_type": "Data",
		"module": "smart_edge_custom",
	}

	if frappe.db.exists("Property Setter", property_setter_name):
		frappe.db.set_value("Property Setter", property_setter_name, values)
		return

	property_setter = frappe.get_doc(
		{
			"doctype": "Property Setter",
			"name": property_setter_name,
			**values,
		}
	)
	property_setter.flags.ignore_permissions = True
	property_setter.insert()
