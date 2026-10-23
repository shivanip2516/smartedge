import frappe


def execute():
	if not frappe.db.exists("DocType", "SmartEdge Production Plan Entry"):
		return
	if not frappe.db.has_column("SmartEdge Production Plan Entry", "plan_type"):
		return

	frappe.db.sql(
		"""
		update `tabSmartEdge Production Plan Entry`
		set plan_type = 'Final EB'
		where ifnull(plan_type, '') = ''
		"""
	)
