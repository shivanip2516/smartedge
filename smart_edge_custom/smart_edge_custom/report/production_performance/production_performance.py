import frappe
from frappe import _
from smart_edge_custom.smart_edge_custom.report.production_reporting import jobs, link, pct, plans, summary, validate


def execute(filters=None):
	filters = validate(filters, True)
	planned = plans(filters)
	actual = jobs(filters)
	rows = []
	for row in planned:
		rows.append(frappe._dict(date=row.planned_date, item=row.material_item or row.final_eb_bom_generator,
			operation=row.operation, workstation=row.workstation, production_job=row.production_job,
			planned_meter=row.planned_meter, planned_kg=row.planned_kg, actual_meter=0, actual_kg=0,
			planned_jobs=1, completed_jobs=0, pending_jobs=0))
	for row in actual:
		if filters.get("production_job") and row.name != filters.production_job: continue
		item = frappe._dict(name= None)
		item_code = None
		if row.final_eb_bom_generator: item_code = frappe.db.get_value("Final EB BOM Generator", row.final_eb_bom_generator, "finished_item")
		elif row.plan_entry: item_code = frappe.db.get_value("SmartEdge Production Plan Entry", row.plan_entry, "material_item")
		if filters.get("finished_item") and item_code != filters.finished_item: continue
		rows.append(frappe._dict(date=frappe.utils.getdate(row.actual_end or row.actual_start), item=item_code,
			operation=row.operation, workstation=row.workstation, production_job=row.name, planned_meter=0,
			planned_kg=0, actual_meter=row.actual_qty_meter, actual_kg=row.actual_qty_kg,
			planned_jobs=0, completed_jobs=1 if row.status == "Completed" else 0, pending_jobs=1 if row.status != "Completed" else 0,
			production_job_link=link("SmartEdge Production Job", row.name)))
	rows = summary(rows, ["date", "item", "operation", "workstation", "production_job"] if filters.get("group_by") == "Production Job" else ["date"])
	for row in rows:
		row.meter_variance = row.actual_meter - row.planned_meter
		row.meter_achievement = pct(row.actual_meter, row.planned_meter)
		row.kg_variance = row.actual_kg - row.planned_kg
		row.kg_achievement = pct(row.actual_kg, row.planned_kg)
	columns = [
		{"label":_("Date"),"fieldname":"date","fieldtype":"Date","width":95}, {"label":_("Item"),"fieldname":"item","fieldtype":"Link","options":"Item","width":150},
		{"label":_("Operation"),"fieldname":"operation","fieldtype":"Link","options":"Operation","width":120}, {"label":_("Workstation"),"fieldname":"workstation","fieldtype":"Link","options":"Workstation","width":120},
		{"label":_("Production Job"),"fieldname":"production_job","fieldtype":"Link","options":"SmartEdge Production Job","width":150},
		{"label":_("Planned Meter"),"fieldname":"planned_meter","fieldtype":"Float"},{"label":_("Actual Meter"),"fieldname":"actual_meter","fieldtype":"Float"},{"label":_("Meter Variance"),"fieldname":"meter_variance","fieldtype":"Float"},{"label":_("Meter Achievement %"),"fieldname":"meter_achievement","fieldtype":"Percent"},
		{"label":_("Planned Kg"),"fieldname":"planned_kg","fieldtype":"Float"},{"label":_("Actual Kg"),"fieldname":"actual_kg","fieldtype":"Float"},{"label":_("Kg Variance"),"fieldname":"kg_variance","fieldtype":"Float"},{"label":_("Kg Achievement %"),"fieldname":"kg_achievement","fieldtype":"Percent"},
		{"label":_("Planned Jobs"),"fieldname":"planned_jobs","fieldtype":"Int"},{"label":_("Completed Jobs"),"fieldname":"completed_jobs","fieldtype":"Int"},{"label":_("Pending Jobs"),"fieldname":"pending_jobs","fieldtype":"Int"}]
	return columns, rows, None, None, [{"label":_("Planned Meter"),"value":sum(r.planned_meter for r in rows),"datatype":"Float"},{"label":_("Actual Meter"),"value":sum(r.actual_meter for r in rows),"datatype":"Float"},{"label":_("Planned Kg"),"value":sum(r.planned_kg for r in rows),"datatype":"Float"},{"label":_("Actual Kg"),"value":sum(r.actual_kg for r in rows),"datatype":"Float"}]
