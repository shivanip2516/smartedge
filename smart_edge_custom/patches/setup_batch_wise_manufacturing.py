import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"BOM": [
				{
					"fieldname": "custom_batch",
					"fieldtype": "Int",
					"label": "Batch",
					"insert_after": "uom",
					"default": "1",
					"read_only": 1,
					"module": "smart_edge_custom",
				},
			],
			"Work Order": [
				{
					"fieldname": "custom_no_of_batches",
					"fieldtype": "Int",
					"label": "No. of Batches",
					"insert_after": "company",
					"default": "1",
					"non_negative": 1,
					"module": "smart_edge_custom",
				},
				{
					"fieldname": "custom_weight_per_batch",
					"fieldtype": "Float",
					"label": "Weight per Batch",
					"insert_after": "custom_no_of_batches",
					"read_only": 1,
					"non_negative": 1,
					"module": "smart_edge_custom",
				},
			],
		},
		update=True,
	)

	frappe.db.set_value(
		"Custom Field", "BOM-custom_process_2", "insert_after", "custom_batch", update_modified=False
	)
	frappe.clear_cache(doctype="BOM")
	frappe.clear_cache(doctype="Work Order")
