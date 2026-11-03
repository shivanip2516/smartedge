import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	fields = [
		{"fieldname": "custom_batch_purpose", "label": "Batch Purpose", "fieldtype": "Select",
			"options": "Physical Stock\nOperation Traceability", "default": "Physical Stock", "in_list_view": 1, "in_standard_filter": 1},
		{"fieldname": "custom_operation_lot", "label": "Operation Lot", "fieldtype": "Link", "options": "SmartEdge Production Lot", "unique": 1},
		{"fieldname": "custom_operation_name", "label": "Operation Name", "fieldtype": "Link", "options": "Operation", "in_list_view": 1},
		{"fieldname": "custom_operation_sequence", "label": "Operation Sequence", "fieldtype": "Int"},
		{"fieldname": "custom_operation_output_quantity", "label": "Operation Output Quantity (Meter)", "fieldtype": "Float", "precision": "6", "in_list_view": 1},
		{"fieldname": "custom_operation_completed_at", "label": "Operation Completion", "fieldtype": "Datetime"},
		{"fieldname": "custom_previous_operation_batch", "label": "Previous Operation Batch", "fieldtype": "Link", "options": "Batch"},
		{"fieldname": "custom_related_fg_batch", "label": "Related Final FG Batch", "fieldtype": "Link", "options": "Batch"},
	]
	after = "disabled"
	for field in fields:
		field.update({"read_only": 1, "module": "smart_edge_custom", "insert_after": after})
		after = field["fieldname"]
	create_custom_fields({"Batch": fields}, update=True)
	frappe.clear_cache(doctype="Batch")
