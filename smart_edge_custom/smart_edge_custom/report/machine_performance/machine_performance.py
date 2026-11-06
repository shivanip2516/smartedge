import frappe
from frappe import _
from smart_edge_custom.smart_edge_custom.report.production_reporting import hours, jobs, pct, summary, validate

def execute(filters=None):
	filters=validate(filters, True); result=[]
	for j in jobs(filters):
		downtime=frappe.db.sql("""select coalesce(sum(duration_minutes),0) from `tabSmartEdge Production Downtime` where parent=%s""", j.name)[0][0] if frappe.db.table_exists("SmartEdge Production Downtime") else j.downtime_minutes
		running=hours(j.actual_start,j.actual_end)
		if running is not None: running=max(0, running-float(downtime or 0)/60)
		result.append(frappe._dict(workstation=j.workstation or "Unassigned",operation=j.operation,production_job=j.name,planned_output=j.planned_qty_meter or j.planned_qty_kg,actual_output=j.actual_qty_meter or j.actual_qty_kg,planned_hours=hours(j.planned_start,j.planned_end),actual_running_hours=running,downtime_hours=float(downtime or 0)/60 if downtime is not None else None,available_hours=None,idle_hours=None,utilization=None,production_jobs=1))
	rows=summary(result,["workstation","operation"])
	for r in rows:
		r.output_per_running_hour=(r.actual_output/r.actual_running_hours) if r.actual_running_hours else None
	columns=[{"label":_("Workstation"),"fieldname":"workstation","fieldtype":"Link","options":"Workstation"},{"label":_("Operation"),"fieldname":"operation","fieldtype":"Link","options":"Operation"},{"label":_("Planned Output"),"fieldname":"planned_output","fieldtype":"Float"},{"label":_("Actual Output"),"fieldname":"actual_output","fieldtype":"Float"},{"label":_("Planned Hours"),"fieldname":"planned_hours","fieldtype":"Float"},{"label":_("Actual Running Hours"),"fieldname":"actual_running_hours","fieldtype":"Float"},{"label":_("Downtime Hours"),"fieldname":"downtime_hours","fieldtype":"Float"},{"label":_("Available Hours"),"fieldname":"available_hours","fieldtype":"Float"},{"label":_("Idle Hours"),"fieldname":"idle_hours","fieldtype":"Float"},{"label":_("Utilization %"),"fieldname":"utilization","fieldtype":"Percent"},{"label":_("Output / Running Hour"),"fieldname":"output_per_running_hour","fieldtype":"Float"},{"label":_("Jobs"),"fieldname":"production_jobs","fieldtype":"Int"}]
	return columns,rows,None,None,[{"label":_("Workstations"),"value":len(rows),"datatype":"Int"}]
