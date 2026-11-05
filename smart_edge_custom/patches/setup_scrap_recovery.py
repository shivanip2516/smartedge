import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	batch_fields = [
		{"fieldname": "custom_smartedge_recovery_section", "label": "SmartEdge Scrap Recovery", "fieldtype": "Section Break", "insert_after": "custom_related_fg_batch", "read_only": 1},
		{"fieldname": "custom_smartedge_recovery_stage", "label": "Recovery Stage", "fieldtype": "Select", "options": "Usable Scrap\nUnusable Scrap\nGround Material\nRecovered Material", "read_only": 1, "in_list_view": 1, "in_standard_filter": 1},
		{"fieldname": "custom_smartedge_source_batch", "label": "Recovery Source Batch", "fieldtype": "Link", "options": "Batch", "read_only": 1},
		{"fieldname": "custom_smartedge_source_production_job", "label": "Source Production Job", "fieldtype": "Link", "options": "SmartEdge Production Job", "read_only": 1},
		{"fieldname": "custom_smartedge_usable_scrap_lot", "label": "Usable Scrap Lot", "fieldtype": "Link", "options": "SmartEdge Usable Scrap Lot", "read_only": 1},
		{"fieldname": "custom_smartedge_scrap_recovery", "label": "Scrap Recovery", "fieldtype": "Link", "options": "SmartEdge Scrap Recovery", "read_only": 1},
		{"fieldname": "custom_smartedge_unusable_scrap_lot", "label": "Unusable Scrap Lot", "fieldtype": "Link", "options": "SmartEdge Unusable Scrap Lot", "read_only": 1},
	]
	stock_entry_fields = [
		{"fieldname": "custom_smartedge_recovery_section", "label": "SmartEdge Scrap Recovery", "fieldtype": "Section Break", "insert_after": "remarks", "read_only": 1},
		{"fieldname": "custom_smartedge_recovery_stage", "label": "Recovery Stage", "fieldtype": "Select", "options": "Usable Scrap Collection\nGrinder\nPulverizer\nUnusable Scrap Storage\nUnusable Scrap Disposal", "read_only": 1, "in_list_view": 1, "in_standard_filter": 1},
		{"fieldname": "custom_smartedge_scrap_movement_type", "label": "Scrap Movement Type", "fieldtype": "Select", "options": "Usable Scrap Generated\nUnusable Scrap Generated\nUsable Scrap Transferred to Grinder\nGrinder Output\nPulverizer Output\nRecovered Material Stock Receipt\nRecovered Material Reused\nUnusable Scrap Transferred to Storage\nUnusable Scrap Disposal\nScrap Movement Reversal", "read_only": 1, "in_standard_filter": 1},
		{"fieldname": "custom_smartedge_production_job", "label": "Source Production Job", "fieldtype": "Link", "options": "SmartEdge Production Job", "read_only": 1},
		{"fieldname": "custom_smartedge_source_batch", "label": "Recovery Source Batch", "fieldtype": "Link", "options": "Batch", "read_only": 1},
		{"fieldname": "custom_smartedge_usable_scrap_lot", "label": "Usable Scrap Lot", "fieldtype": "Link", "options": "SmartEdge Usable Scrap Lot", "read_only": 1},
		{"fieldname": "custom_smartedge_scrap_recovery", "label": "Scrap Recovery", "fieldtype": "Link", "options": "SmartEdge Scrap Recovery", "read_only": 1},
		{"fieldname": "custom_smartedge_unusable_scrap_lot", "label": "Unusable Scrap Lot", "fieldtype": "Link", "options": "SmartEdge Unusable Scrap Lot", "read_only": 1},
		{"fieldname": "custom_smartedge_source_production_lot", "label": "Source Production Lot", "fieldtype": "Link", "options": "SmartEdge Production Lot", "read_only": 1},
		{"fieldname": "custom_smartedge_source_operation", "label": "Source Operation", "fieldtype": "Link", "options": "Operation", "read_only": 1},
		{"fieldname": "custom_smartedge_source_workstation", "label": "Source Workstation", "fieldtype": "Link", "options": "Workstation", "read_only": 1},
		{"fieldname": "custom_smartedge_source_employee", "label": "Source Employee", "fieldtype": "Link", "options": "Employee", "read_only": 1},
		{"fieldname": "custom_smartedge_movement_workstation", "label": "Movement Workstation", "fieldtype": "Link", "options": "Workstation", "read_only": 1},
		{"fieldname": "custom_smartedge_movement_employee", "label": "Movement Employee", "fieldtype": "Link", "options": "Employee", "read_only": 1},
		{"fieldname": "custom_smartedge_scrap_request_id", "label": "Scrap Request ID", "fieldtype": "Data", "read_only": 1},
		{"fieldname": "custom_smartedge_disposal_reason", "label": "Scrap Disposal Reason", "fieldtype": "Data", "read_only": 1},
	]
	quality_fields = [
		{"fieldname": "custom_smartedge_scrap_recovery", "label": "Scrap Recovery", "fieldtype": "Link", "options": "SmartEdge Scrap Recovery", "insert_after": "custom_rework_resubmitted_to", "read_only": 1},
	]
	for fields in (batch_fields, stock_entry_fields, quality_fields):
		for field in fields:
			field.setdefault("module", "smart_edge_custom")
	create_custom_fields({
		"Batch": batch_fields,
		"Stock Entry": stock_entry_fields,
		"Quality Inspection": quality_fields,
	}, update=True)
	for doctype in ("Batch", "Stock Entry", "Quality Inspection"):
		frappe.clear_cache(doctype=doctype)
