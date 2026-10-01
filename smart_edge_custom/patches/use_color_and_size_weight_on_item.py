import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	ensure_item_fields()
	update_customer_order_code_master()
	migrate_exact_color_values()
	frappe.clear_cache(doctype="Item")
	frappe.clear_cache(doctype="Customer Order Code")


def ensure_item_fields():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_color",
					"fieldtype": "Link",
					"label": "Color",
					"insert_after": "custom_size",
					"options": "Color",
					"reqd": 1,
					"allow_in_quick_entry": 1,
					"in_global_search": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"module": "smart_edge_custom",
				},
			]
		},
		update=True,
	)

	if frappe.db.exists("Custom Field", "Item-custom_size"):
		frappe.db.set_value(
			"Custom Field",
			"Item-custom_size",
			{
				"fieldtype": "Link",
				"options": "Size Weight",
				"label": "Size",
				"insert_after": "pvc_edge_band_details_section",
				"reqd": 1,
				"module": "smart_edge_custom",
			},
		)

	for fieldname in ("custom_base_color", "custom_base_colour"):
		custom_field = "Item-{0}".format(fieldname)
		if not frappe.db.exists("Custom Field", custom_field):
			continue

		frappe.db.set_value(
			"Custom Field",
			custom_field,
			{
				"hidden": 1,
				"reqd": 0,
				"read_only": 1,
				"allow_in_quick_entry": 0,
				"in_global_search": 0,
				"in_list_view": 0,
				"in_standard_filter": 0,
				"module": "smart_edge_custom",
			},
		)

	if frappe.db.exists("Custom Field", "Item-custom_product_category"):
		frappe.db.set_value(
			"Custom Field",
			"Item-custom_product_category",
			"insert_after",
			"custom_color",
		)


def update_customer_order_code_master():
	if not frappe.db.exists("DocType", "Customer Order Code"):
		return

	doc = frappe.get_doc("DocType", "Customer Order Code")
	for field in doc.fields:
		if field.fieldname == "color":
			field.options = "Color"
			break

	doc.save()


def migrate_exact_color_values():
	if not (
		frappe.db.has_column("Item", "custom_color")
		and frappe.db.has_column("Item", "custom_base_color")
		and frappe.db.has_column("Item", "custom_base_colour")
	):
		return

	frappe.db.sql(
		"""
		update `tabItem`
		set custom_color = custom_base_color
		where ifnull(custom_color, '') = ''
			and ifnull(custom_base_color, '') != ''
			and custom_base_color in (select name from `tabColor`)
		"""
	)
	frappe.db.sql(
		"""
		update `tabItem`
		set custom_color = custom_base_colour
		where ifnull(custom_color, '') = ''
			and ifnull(custom_base_colour, '') != ''
			and custom_base_colour in (select name from `tabColor`)
		"""
	)
