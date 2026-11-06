import frappe
from frappe import _
from smart_edge_custom.smart_edge_custom.report.production_reporting import hours, jobs, summary, validate

def execute(filters=None):
	filters=validate(filters,True); out=[]
	for j in jobs(filters):
		duration=hours(j.actual_start,j.actual_end); downtime=float(j.downtime_minutes or 0)/60 if j.downtime_minutes is not None else None
		productive=max(0,duration-downtime) if duration is not None and downtime is not None else (float(j.productive_elapsed_seconds)/3600 if j.productive_elapsed_seconds is not None else None)
		out.append(frappe._dict(employee=j.operator_employee or "Unassigned",employee_name=j.operator_name,workstation=j.workstation,operation=j.operation,shift=j.shift,production_job=j.name,actual_output=j.actual_qty_meter or j.actual_qty_kg,productive_running_hours=productive,downtime_hours=downtime,total_job_duration=duration,completed_job_count=1 if j.status=="Completed" else 0))
	rows=summary(out,["employee","workstation","operation","shift"])
	for r in rows:r.output_per_productive_hour=r.actual_output/r.productive_running_hours if r.productive_running_hours else None
	cols=[{"label":_("Employee"),"fieldname":"employee","fieldtype":"Link","options":"Employee"},{"label":_("Employee Name"),"fieldname":"employee_name","fieldtype":"Data"},{"label":_("Workstation"),"fieldname":"workstation","fieldtype":"Link","options":"Workstation"},{"label":_("Operation"),"fieldname":"operation","fieldtype":"Link","options":"Operation"},{"label":_("Shift"),"fieldname":"shift","fieldtype":"Link","options":"Shift Type"},{"label":_("Actual Output"),"fieldname":"actual_output","fieldtype":"Float"},{"label":_("Productive Running Hours"),"fieldname":"productive_running_hours","fieldtype":"Float"},{"label":_("Downtime Hours"),"fieldname":"downtime_hours","fieldtype":"Float"},{"label":_("Job Duration"),"fieldname":"total_job_duration","fieldtype":"Float"},{"label":_("Output / Productive Hour"),"fieldname":"output_per_productive_hour","fieldtype":"Float"},{"label":_("Completed Jobs"),"fieldname":"completed_job_count","fieldtype":"Int"}]
	return cols,rows,None,None,None
