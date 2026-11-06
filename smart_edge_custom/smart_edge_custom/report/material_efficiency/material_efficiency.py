import frappe
from frappe import _
from frappe.utils import flt
from smart_edge_custom.smart_edge_custom.report.production_reporting import pct, summary, validate

def execute(filters=None):
	filters=validate(filters,True); rows=[]
	for j in frappe.get_list("SmartEdge Production Job",filters=[["SmartEdge Production Job","actual_start",">=",filters.from_date],["SmartEdge Production Job","actual_start","<",frappe.utils.add_days(filters.to_date,1)]],fields=["name","final_eb_bom_generator","operation","workstation","actual_qty_meter","actual_qty_kg"],limit_page_length=0):
		g=frappe.db.get_value("Final EB BOM Generator",j.final_eb_bom_generator,["finished_item","weight_per_meter_kg","total_eb_weight_kg","production_quantity"],as_dict=True) if j.final_eb_bom_generator else None
		if filters.get("item") and (not g or g.finished_item != filters.item): continue
		standard=flt(g.weight_per_meter_kg) if g and g.weight_per_meter_kg else None
		actual=flt(j.actual_qty_kg)/flt(j.actual_qty_meter) if j.actual_qty_meter and j.actual_qty_kg else None
		rows.append(frappe._dict(finished_item=g.finished_item if g else None,operation=j.operation,workstation=j.workstation,production_job=j.name,standard_kg_per_meter=standard,actual_kg_per_meter=actual,standard_material_consumption=standard*flt(j.actual_qty_meter) if standard is not None and j.actual_qty_meter else None,actual_material_consumption=j.actual_qty_kg,material_variance=None,material_variance_percent=None,good_output_quantity=j.actual_qty_meter,yield_percent=None,process_loss_percent=None))
	rows=summary(rows,["finished_item","operation","workstation"])
	for r in rows:
		if r.standard_material_consumption is not None and r.actual_material_consumption is not None:r.material_variance=r.actual_material_consumption-r.standard_material_consumption;r.material_variance_percent=pct(r.material_variance,r.standard_material_consumption)
	cols=[{"label":_("Finished Item"),"fieldname":"finished_item","fieldtype":"Link","options":"Item"},{"label":_("Operation"),"fieldname":"operation","fieldtype":"Link","options":"Operation"},{"label":_("Workstation"),"fieldname":"workstation","fieldtype":"Link","options":"Workstation"},{"label":_("Standard Kg/Meter"),"fieldname":"standard_kg_per_meter","fieldtype":"Float"},{"label":_("Actual Kg/Meter"),"fieldname":"actual_kg_per_meter","fieldtype":"Float"},{"label":_("Standard Consumption"),"fieldname":"standard_material_consumption","fieldtype":"Float"},{"label":_("Actual Consumption"),"fieldname":"actual_material_consumption","fieldtype":"Float"},{"label":_("Material Variance"),"fieldname":"material_variance","fieldtype":"Float"},{"label":_("Material Variance %"),"fieldname":"material_variance_percent","fieldtype":"Percent"},{"label":_("Yield %"),"fieldname":"yield_percent","fieldtype":"Percent"},{"label":_("Process Loss %"),"fieldname":"process_loss_percent","fieldtype":"Percent"}]
	return cols,rows,None,None,None
