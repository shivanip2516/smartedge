import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"BOM": [
				{
					"fieldname": "custom_process_2",
					"fieldtype": "Select",
					"label": "Process",
					"insert_after": "uom",
					"options": "\nExtrusion\nExtrusion Online Printing\nOffline Printing",
					"reqd": 0,
					"module": "smart_edge_custom",
				}
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="BOM")
