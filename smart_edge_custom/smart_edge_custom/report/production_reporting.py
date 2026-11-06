"""Shared, read-only data access for SmartEdge Phase 8 reports.

The report layer deliberately reads the existing execution records.  It does
not create reporting snapshots or alter manufacturing transactions.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, getdate


def validate(filters, required_company=False):

	filters = frappe._dict(filters or {})
	filters.from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
	filters.to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are required."))
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if (filters.to_date - filters.from_date).days > 366:
		frappe.throw(_("Select a reporting period of 366 days or less."))
	if required_company and not filters.company:
		frappe.throw(_("Company is required."))
	frappe.has_permission("SmartEdge Production Job", "read", throw=True)
	return filters


def pct(numerator, denominator):
	return round(flt(numerator) * 100 / flt(denominator), 2) if denominator else None


def hours(start, end):
	if not start or not end:
		return None
	return max(0, (frappe.utils.get_datetime(end) - frappe.utils.get_datetime(start)).total_seconds() / 3600)


def jobs(filters, completed_only=False):
	conditions = [["SmartEdge Production Job", "actual_start", ">=", filters.from_date],
		["SmartEdge Production Job", "actual_start", "<", filters.to_date + timedelta(days=1)]]
	if completed_only:
		conditions.append(["SmartEdge Production Job", "status", "=", "Completed"])
	for fieldname in ("company", "workstation", "operation", "shift", "operator_employee"):
		if filters.get(fieldname):
			conditions.append(["SmartEdge Production Job", fieldname, "=", filters[fieldname]])
	return frappe.get_list("SmartEdge Production Job", filters=conditions, fields=[
		"name", "company", "plan_entry", "final_eb_bom_generator", "operation", "workstation", "shift",
		"operator_employee", "operator_name", "actual_start", "actual_end", "planned_start", "planned_end",
		"planned_qty_meter", "actual_qty_meter", "planned_qty_kg", "actual_qty_kg", "good_qty", "rejected_qty",
		"rework_qty", "downtime_minutes", "productive_elapsed_seconds", "status", "actual_duration_hours",
	], limit_page_length=0, order_by="actual_start asc, name asc")


def plans(filters):
	conditions = [["SmartEdge Production Plan Entry", "planned_date", ">=", filters.from_date],
		["SmartEdge Production Plan Entry", "planned_date", "<=", filters.to_date]]
	for fieldname in ("workstation", "operation", "planned_shift"):
		if filters.get(fieldname):
			conditions.append(["SmartEdge Production Plan Entry", fieldname, "=", filters[fieldname]])
	return frappe.get_list("SmartEdge Production Plan Entry", filters=conditions, fields=[
		"name", "final_eb_bom_generator", "material_item", "plan_type", "operation", "workstation",
		"planned_shift", "planned_date", "planned_meter", "planned_kg", "production_quantity", "quantity_uom",
		"status", "production_job",
	], limit_page_length=0)


def item_for_job(job):
	if job.get("final_eb_bom_generator"):
		return frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "finished_item")
	if job.get("plan_entry"):
		return frappe.db.get_value("SmartEdge Production Plan Entry", job.plan_entry, "material_item")
	return None


def link(doctype, name):
	return f"/app/{frappe.scrub(doctype)}/{name}" if name else None


def summary(rows, keys):
	result = {}
	for row in rows:
		key = tuple(row.get(k) for k in keys)
		if key not in result:
			result[key] = frappe._dict(row)
		else:
			for field in row:
				if field not in keys and isinstance(row.get(field), (int, float)):
					result[key][field] = flt(result[key].get(field)) + flt(row.get(field))
	return list(result.values())
