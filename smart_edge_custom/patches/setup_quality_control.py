import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	reference_options = frappe.get_meta("Quality Inspection").get_field("reference_type").options or ""
	if "SmartEdge Production Job" not in reference_options.split("\n"):
		reference_options = f"{reference_options.rstrip()}\nSmartEdge Production Job"
	make_property_setter(
		"Quality Inspection", "reference_type", "options", reference_options, "Text"
	)
	fields = [
		{"fieldname": "custom_smartedge_qc_section", "label": "SmartEdge In-Process QC", "fieldtype": "Section Break", "insert_after": "remarks"},
		{"fieldname": "custom_smartedge_qc_key", "label": "SmartEdge QC Key", "fieldtype": "Data", "unique": 1, "read_only": 1, "hidden": 1},
		{"fieldname": "custom_final_eb_bom_generator", "label": "Final EB BOM Generator", "fieldtype": "Link", "options": "Final EB BOM Generator", "read_only": 1},
		{"fieldname": "custom_qc_plan_entry", "label": "QC Plan Entry", "fieldtype": "Link", "options": "SmartEdge Production Plan Entry", "read_only": 1},
		{"fieldname": "custom_source_production_job", "label": "Source Production Job", "fieldtype": "Link", "options": "SmartEdge Production Job", "read_only": 1},
		{"fieldname": "custom_source_production_lot", "label": "Production Lot", "fieldtype": "Link", "options": "SmartEdge Production Lot", "read_only": 1},
		{"fieldname": "custom_source_batch", "label": "Operation Batch", "fieldtype": "Link", "options": "Batch", "read_only": 1},
		{"fieldname": "custom_qc_operation", "label": "QC Checkpoint", "fieldtype": "Link", "options": "Operation", "read_only": 1},
		{"fieldname": "custom_smartedge_qc_status", "label": "QC Status", "fieldtype": "Select", "options": "Pending\nUnder Review\nAccepted\nRejected\nRework Required", "read_only": 1, "in_list_view": 1, "in_standard_filter": 1},
		{"fieldname": "custom_qc_employee", "label": "QC Employee", "fieldtype": "Link", "options": "Employee", "read_only": 1},
		{"fieldname": "custom_inspection_datetime", "label": "Inspection Date and Time", "fieldtype": "Datetime", "read_only": 1},
		{"fieldname": "custom_smartedge_measurements", "label": "Measurements and Decision", "fieldtype": "Section Break"},
		{"fieldname": "custom_width", "label": "Width", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_thickness", "label": "Thickness", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_color_match", "label": "Color Match", "fieldtype": "Select", "options": "\nPass\nFail\nNot Applicable", "read_only": 1},
		{"fieldname": "custom_design_match", "label": "Design Match", "fieldtype": "Select", "options": "\nPass\nFail\nNot Applicable", "read_only": 1},
		{"fieldname": "custom_surface_appearance", "label": "Surface / Appearance", "fieldtype": "Select", "options": "\nPass\nFail\nNot Applicable", "read_only": 1},
		{"fieldname": "custom_print_quality", "label": "Print Quality", "fieldtype": "Select", "options": "\nPass\nFail\nNot Applicable", "read_only": 1},
		{"fieldname": "custom_eligible_qty", "label": "Eligible Quantity", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_inspected_qty", "label": "Inspected Quantity", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_accepted_qty", "label": "Accepted Quantity", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_rejected_qty", "label": "Rejected Quantity", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_rework_qty", "label": "Rework Quantity", "fieldtype": "Float", "precision": "6", "read_only": 1},
		{"fieldname": "custom_rejection_rework_reason", "label": "Rejection / Rework Reason", "fieldtype": "Small Text", "read_only": 1},
		{"fieldname": "custom_qc_remarks", "label": "QC Remarks", "fieldtype": "Small Text", "read_only": 1},
		{"fieldname": "custom_source_qc", "label": "Reinspection Of", "fieldtype": "Link", "options": "Quality Inspection", "read_only": 1},
		{"fieldname": "custom_rework_resolved", "label": "Rework Resolved", "fieldtype": "Check", "read_only": 1},
		{"fieldname": "custom_rework_resubmitted_to", "label": "Rework Resubmitted To", "fieldtype": "Link", "options": "Quality Inspection", "read_only": 1},
	]
	for field in fields:
		field.setdefault("module", "smart_edge_custom")
	create_custom_fields({"Quality Inspection": fields}, update=True)
	frappe.clear_cache(doctype="Quality Inspection")
