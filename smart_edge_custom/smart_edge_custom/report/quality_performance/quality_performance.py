import frappe
from frappe import _
from smart_edge_custom.smart_edge_custom.report.production_reporting import pct, summary, validate

def execute(filters=None):
	filters=validate(filters,True)
	conditions={"actual_start":[">=",filters.from_date],"actual_end":["<",frappe.utils.add_days(filters.to_date,1)]}
	fields=["name","production_job","finished_item","operation","workstation","employee","actual_start","actual_end","good_quantity","rejected_quantity","rework_quantity","quality_inspection"]
	lots=frappe.get_list("SmartEdge Production Lot",filters=conditions,fields=fields,limit_page_length=0)
	rows=[]
	for l in lots:
		if filters.get("item") and l.finished_item != filters.item: continue
		inspected=sum(float(l.get(x) or 0) for x in ("good_quantity","rejected_quantity","rework_quantity"))
		rows.append(frappe._dict(product=l.finished_item,workstation=l.workstation,operation=l.operation,production_job=l.production_job,quality_inspection=l.quality_inspection,inspected_quantity=inspected,accepted_quantity=l.good_quantity,rejected_quantity=l.rejected_quantity,rework_quantity=l.rework_quantity,qc_failure_count=1 if l.rejected_quantity else 0))
	rows=summary(rows,["product","workstation","operation"])
	for r in rows:r.rejection_percent=pct(r.rejected_quantity,r.inspected_quantity);r.rework_percent=pct(r.rework_quantity,r.inspected_quantity)
	cols=[{"label":_("Product"),"fieldname":"product","fieldtype":"Link","options":"Item"},{"label":_("Workstation"),"fieldname":"workstation","fieldtype":"Link","options":"Workstation"},{"label":_("Operation"),"fieldname":"operation","fieldtype":"Link","options":"Operation"},{"label":_("Inspected Quantity"),"fieldname":"inspected_quantity","fieldtype":"Float"},{"label":_("Accepted Quantity"),"fieldname":"accepted_quantity","fieldtype":"Float"},{"label":_("Rejected Quantity"),"fieldname":"rejected_quantity","fieldtype":"Float"},{"label":_("Rework Quantity"),"fieldname":"rework_quantity","fieldtype":"Float"},{"label":_("Rejection %"),"fieldname":"rejection_percent","fieldtype":"Percent"},{"label":_("Rework %"),"fieldname":"rework_percent","fieldtype":"Percent"},{"label":_("QC Failure Count"),"fieldname":"qc_failure_count","fieldtype":"Int"}]
	return cols,rows,None,None,None
