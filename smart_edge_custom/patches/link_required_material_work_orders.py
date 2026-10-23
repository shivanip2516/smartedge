import frappe

from smart_edge_custom.setup import create_custom_field


WORK_ORDER_FIELDS = {
	"custom_final_eb_bom_generator": {
		"fieldtype": "Link",
		"label": "Final EB BOM Generator",
		"options": "Final EB BOM Generator",
		"insert_after": "project",
		"read_only": 1,
	},
	"custom_material_type": {
		"fieldtype": "Select",
		"label": "Required Material Type",
		"options": "\nCompound\nBase Colour",
		"insert_after": "custom_final_eb_bom_generator",
		"read_only": 1,
	},
	"custom_material_operation": {
		"fieldtype": "Link",
		"label": "Required Material Operation",
		"options": "Operation",
		"insert_after": "custom_material_type",
		"read_only": 1,
	},
	"custom_material_workstation": {
		"fieldtype": "Link",
		"label": "Required Material Workstation",
		"options": "Workstation",
		"insert_after": "custom_material_operation",
		"read_only": 1,
	},
	"custom_material_plan_entry": {
		"fieldtype": "Link",
		"label": "Required Material Plan Entry",
		"options": "SmartEdge Production Plan Entry",
		"insert_after": "custom_material_workstation",
		"read_only": 1,
	},
}


def execute():
	for fieldname, values in WORK_ORDER_FIELDS.items():
		create_custom_field("Work Order", fieldname, values)

	if not frappe.db.exists("DocType", "SmartEdge Production Plan Entry"):
		return
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={"plan_type": "Required Material", "work_order": ["is", "set"]},
		fields=[
			"name",
			"final_eb_bom_generator",
			"material_type",
			"operation",
			"workstation",
			"work_order",
		],
		limit_page_length=10000,
	)
	for row in rows:
		if not frappe.db.exists("Work Order", row.work_order):
			continue
		frappe.db.set_value(
			"Work Order",
			row.work_order,
			{
				"custom_final_eb_bom_generator": row.final_eb_bom_generator,
				"custom_material_type": row.material_type,
				"custom_material_operation": row.operation,
				"custom_material_workstation": row.workstation,
				"custom_material_plan_entry": row.name,
			},
			update_modified=False,
		)

	frappe.db.sql(
		"""
		update `tabFinal EB BOM Generator`
		set material_readiness_status = case
			when material_readiness_status in ('Materials Ready', 'Ready to Plan') then 'Ready to Plan'
			else 'Waiting for Raw Material'
		end
		"""
	)
