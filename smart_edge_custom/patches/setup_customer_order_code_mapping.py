import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	ensure_item_fields()
	migrate_safe_customer_order_codes()
	frappe.clear_cache(doctype="Item")


def ensure_item_fields():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "product_code",
					"fieldtype": "Data",
					"label": "Customer Order Code",
					"insert_after": "item_code",
					"read_only": 1,
					"allow_in_quick_entry": 1,
					"in_global_search": 1,
					"in_list_view": 1,
					"in_standard_filter": 1,
					"module": "smart_edge_custom",
				}
			]
		},
		update=True,
	)

	if frappe.db.exists("Custom Field", "Item-custom_customer_order_code"):
		frappe.db.set_value(
			"Custom Field",
			"Item-custom_customer_order_code",
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


def migrate_safe_customer_order_codes():
	if not (
		frappe.db.has_column("Item", "product_code")
		and frappe.db.has_column("Item", "custom_customer_order_code")
	):
		return

	frappe.db.sql(
		"""
		update `tabItem`
		set product_code = custom_customer_order_code
		where ifnull(product_code, '') = ''
			and ifnull(custom_customer_order_code, '') != ''
		"""
	)
