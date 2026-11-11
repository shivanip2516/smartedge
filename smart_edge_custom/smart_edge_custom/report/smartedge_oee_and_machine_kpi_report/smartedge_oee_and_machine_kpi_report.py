"""Read-only OEE/KPI reporting over existing SmartEdge execution records."""

from collections import defaultdict
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate

from smart_edge_custom.manufacturing.machine_capacity import (
	get_capacity_rate,
	get_rate_payload,
	get_shift_schedule,
	get_workstation_schedule,
)


def _validate(filters):
	filters = frappe._dict(filters or {})
	filters.from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
	filters.to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None
	if not filters.from_date or not filters.to_date:
		frappe.throw(_("From Date and To Date are required."))
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	return filters


def _merge_intervals(intervals):
	valid = sorted((get_datetime(start), get_datetime(end)) for start, end in intervals if start and end and get_datetime(end) > get_datetime(start))
	merged = []
	for start, end in valid:
		if not merged or start > merged[-1][1]:
			merged.append([start, end])
		else:
			merged[-1][1] = max(merged[-1][1], end)
	return merged


def _hours(intervals):
	return sum((end - start).total_seconds() for start, end in intervals) / 3600 if intervals else None


def _downtime_by_job(job_names):
	if not job_names or not frappe.db.table_exists("SmartEdge Production Downtime"):
		return {}
	rows = frappe.get_all(
		"SmartEdge Production Downtime",
		filters={"parent": ["in", job_names], "parenttype": "SmartEdge Production Job"},
		fields=["parent", "start_time", "end_time"], limit_page_length=0,
	)
	intervals = defaultdict(list)
	for row in rows:
		if row.start_time and row.end_time:
			intervals[row.parent].append((row.start_time, row.end_time))
	return {name: _hours(_merge_intervals(values)) for name, values in intervals.items()}


def _jobs(filters):
	conditions = {
		"status": "Completed",
		"actual_start": [">=", filters.from_date],
	}
	end = filters.to_date + timedelta(days=1)
	conditions["actual_start"] = ["between", [filters.from_date, end]]
	for fieldname in ("shift", "workstation", "operation"):
		if filters.get(fieldname):
			conditions[fieldname] = filters[fieldname]
	if filters.get("production_job"):
		conditions["name"] = filters.production_job
	return frappe.get_all("SmartEdge Production Job", filters=conditions, fields=[
		"name", "company", "plan_entry", "final_eb_bom_generator", "operation", "workstation", "shift",
		"actual_start", "actual_end", "planned_start", "planned_end", "productive_elapsed_seconds",
		"planned_qty_meter", "planned_qty_kg", "actual_qty_meter", "actual_qty_kg", "good_qty",
		"rejected_qty", "rework_qty", "usable_scrap", "unusable_scrap", "quality_inspection",
	], limit_page_length=0, order_by="actual_start asc, name asc")


def _get_existing_values(doctype, name, fields):
	"""Read only fields present in this site's DocType schema.

	The production-plan schema has changed over time; an absent optional field
	must produce an unavailable value rather than an invalid SQL column.
	"""
	if not name:
		return frappe._dict()
	meta = frappe.get_meta(doctype, cached=True)
	available = {field.fieldname for field in meta.fields}
	requested = [field for field in fields if field in available]
	if not requested:
		return frappe._dict()
	return frappe.db.get_value(doctype, name, requested, as_dict=True) or frappe._dict()


def _job_context(job):
	plan = _get_existing_values("SmartEdge Production Plan Entry", job.plan_entry, [
		"planning_category", "material_item", "quantity_uom", "production_quantity", "planned_meter", "planned_kg", "custom_size",
	])
	production = _get_existing_values("Final EB BOM Generator", job.final_eb_bom_generator, [
		"finished_item", "custom_size", "total_eb_weight_kg", "stock_uom",
	])
	product = production.get("finished_item") or plan.get("material_item")
	if product:
		item_values = _get_existing_values("Item", product, ["item_name", "stock_uom"])
		production["item_name"] = item_values.get("item_name")
		production["stock_uom"] = production.get("stock_uom") or item_values.get("stock_uom")
	return plan or frappe._dict(), production or frappe._dict(), product


def _rate(job, plan, production, product):
	row = frappe._dict(
		planning_category=plan.get("planning_category"), operation=job.operation, workstation=job.workstation,
		company=job.company, custom_size=plan.get("custom_size") or production.get("custom_size"),
		output_uom=plan.get("output_uom"),
		finished_item=product, planned_kg=job.planned_qty_kg or plan.get("planned_kg") or production.get("total_eb_weight_kg"),
		planned_meter=job.planned_qty_meter or plan.get("planned_meter"), production_quantity=plan.get("production_quantity"),
	)
	payload = get_rate_payload(row)
	if payload.get("configured"):
		return payload
	# Some existing operation labels (for example Packing/Mixing) are not
	# capacity categories, although their workstation has a configured rate.
	# Resolve only the configured unit-specific rate; never invent a rate.
	output_uom = row.get("output_uom")
	capacity_uom = {"Kg": "Kg/Hour", "Meter": "Meter/Hour"}.get(output_uom)
	if not capacity_uom or not job.workstation:
		return None
	match = get_capacity_rate(
		job.workstation,
		capacity_uom,
		product_size=row.get("custom_size"),
		product_item=product,
		company=job.company,
	)
	if not match:
		return None
	return {"configured": 1, "capacity_rate": flt(match.get("capacity_rate")), "capacity_uom": capacity_uom}


def _resolve_output(job, plan, production):
	"""Return (UOM, total output, planned output) without mixing units."""
	good = flt(job.good_qty)
	rejected = flt(job.rejected_qty)
	rework = flt(job.rework_qty)
	components = good + rejected + rework
	meter = flt(job.actual_qty_meter)
	kg = flt(job.actual_qty_kg)
	if meter > 0 and kg > 0:
		return None, None, None
	if meter > 0:
		return "Meter", meter, flt(job.planned_qty_meter)
	if kg > 0:
		return "Kg", kg, flt(job.planned_qty_kg)
	# Completed operation records can have the measured total unset while the
	# validated completion quantities are present. Use those same quantities,
	# with UOM taken from the existing plan/generator metadata.
	uom = plan.get("quantity_uom") or production.get("stock_uom")
	if components <= 0 or not uom:
		return None, None, None
	normalized = str(uom).strip().lower()
	if normalized in {"kg", "kilogram", "kilograms"}:
		return "Kg", components, flt(job.planned_qty_kg or plan.get("planned_kg") or production.get("total_eb_weight_kg"))
	if normalized in {"m", "meter", "meters"}:
		return "Meter", components, flt(job.planned_qty_meter or plan.get("planned_meter"))
	return None, None, None


def _safe_pct(numerator, denominator):
	return None if numerator is None or denominator is None or flt(denominator) <= 0 else flt(numerator) * 100 / flt(denominator)


def execute(filters=None):
	filters = _validate(filters)
	jobs = _jobs(filters)
	downtime = _downtime_by_job([job.name for job in jobs])
	groups = {}
	warnings = []
	for job in jobs:
		# QC checkpoints are quality gates, not machine utilization by default.
		if not job.workstation or (job.operation and str(job.operation).upper().startswith("QC")):
			continue
		plan, production, product = _job_context(job)
		if filters.get("product") and product != filters.product:
			continue
		output_uom, actual_output, planned_output = _resolve_output(job, plan, production)
		if planned_output is None and output_uom == "Meter":
			planned_output = flt(plan.get("planned_meter") or plan.get("production_quantity")) or None
		elif planned_output is None and output_uom == "Kg":
			planned_output = flt(plan.get("planned_kg") or production.get("total_eb_weight_kg")) or None
		running = flt(job.productive_elapsed_seconds) / 3600 if flt(job.productive_elapsed_seconds) > 0 else None
		downtime_hours = downtime.get(job.name)
		planned_hours = ((get_datetime(job.planned_end) - get_datetime(job.planned_start)).total_seconds() / 3600
			if job.planned_start and job.planned_end else None)
		available_intervals, configured, schedule_message, holiday = get_workstation_schedule(job.workstation, getdate(job.actual_start))
		if job.shift:
			shift_intervals, shift_configured, shift_message, shift_holiday = get_shift_schedule(job.shift, getdate(job.actual_start))
			available_intervals = [(max(a, b), min(c, d)) for a, c in available_intervals for b, d in shift_intervals if max(a, b) < min(c, d)] if available_intervals and shift_intervals else []
		available_hours = _hours(available_intervals) if configured else None
		if not configured and schedule_message:
			warnings.append(f"{job.workstation}: {schedule_message}")
		payload = _rate(job, {**plan, "output_uom": output_uom}, production, product)
		ideal_rate = payload.get("capacity_rate") if payload else None
		first_pass_good = flt(job.good_qty) if job.good_qty is not None else None
		if first_pass_good is not None and actual_output is not None and first_pass_good > actual_output:
			warnings.append(f"{job.name}: first-pass good exceeds total produced output; quality is unavailable")
			first_pass_good = None
		job_date = getdate(job.actual_start)
		key = (job_date, job.workstation, job.operation, product, output_uom, job.shift)
		row = groups.setdefault(key, frappe._dict(
			date=job_date, shift=job.shift, workstation=job.workstation, operation=job.operation,
			product=product, product_name=production.get("item_name"), output_uom=output_uom, planned_output=0, actual_output=0, first_pass_good=0,
			rejected_quantity=0, rework_quantity=0, usable_scrap=0, unusable_scrap=0, planned_hours=0,
			available_hours=0, actual_running_hours=0, downtime_hours=0, completed_jobs=0,
			_availability_intervals=[],
			_runtime_missing=False, _planned_missing=False, _available_missing=False, _rate_missing=False,
		))
		row._availability_intervals.extend(available_intervals)
		for field, value in (("planned_output", planned_output), ("actual_output", actual_output), ("first_pass_good", first_pass_good), ("planned_hours", planned_hours), ("running", running), ("downtime", downtime_hours), ("ideal_rate", ideal_rate)):
			if value is None:
				row._runtime_missing |= field == "running"; row._planned_missing |= field == "planned_hours"; row._available_missing |= field == "available_hours"; row._rate_missing |= field == "ideal_rate"
			elif field == "running": row.actual_running_hours += value
			elif field == "downtime": row.downtime_hours += value
			elif field == "ideal_rate": row.ideal_rate = value if not row.get("ideal_rate") else row.ideal_rate
			else: row[field] += value
		row.rejected_quantity += flt(job.rejected_qty); row.rework_quantity += flt(job.rework_qty); row.usable_scrap += flt(job.usable_scrap); row.unusable_scrap += flt(job.unusable_scrap); row.completed_jobs += 1
	for row in groups.values():
		row.available_hours = None if row._available_missing else (_hours(_merge_intervals(row._availability_intervals)) if row._availability_intervals else None)
		row.actual_running_hours = None if row._runtime_missing else row.actual_running_hours
		row.planned_hours = None if row._planned_missing else row.planned_hours
		row.idle_hours = (max(row.available_hours - row.actual_running_hours - row.downtime_hours, 0) if row.available_hours is not None and row.actual_running_hours is not None else None)
		row.actual_per_running_hour = row.actual_output / row.actual_running_hours if row.actual_output is not None and row.actual_running_hours else None
		row.machine_utilization = _safe_pct(row.actual_running_hours, row.available_hours)
		row.availability_percent = _safe_pct(row.actual_running_hours, row.planned_hours)
		row.performance_percent = (row.actual_output * 100 / (row.ideal_rate * row.actual_running_hours) if row.actual_output is not None and row.ideal_rate and row.actual_running_hours else None)
		row.quality_percent = _safe_pct(row.first_pass_good, row.actual_output)
		row.total_scrap = row.usable_scrap + row.unusable_scrap
		row.good_output_percent = row.quality_percent
		row.scrap_percent = _safe_pct(row.total_scrap, row.actual_output)
		row.rework_percent = _safe_pct(row.rework_quantity, row.actual_output)
		row.oee_percent = (row.availability_percent * row.performance_percent * row.quality_percent / 10000 if None not in (row.availability_percent, row.performance_percent, row.quality_percent) else None)
		row.runtime_overrun_hours = max(row.actual_running_hours - row.planned_hours, 0) if row.actual_running_hours is not None and row.planned_hours is not None else None
		for key in ("_runtime_missing", "_planned_missing", "_available_missing", "_rate_missing", "_availability_intervals"):
			row.pop(key, None)
	column_defs = [
		("Date", "date", "Date"), ("Shift", "shift", "Link", "Shift Type"), ("Workstation", "workstation", "Link", "Workstation"), ("Operation", "operation", "Link", "Operation"), ("Product", "product", "Link", "Item"), ("Product Name", "product_name", "Data"), ("Output UOM", "output_uom", "Data"),
		("Planned Output", "planned_output", "Float"), ("Actual Output", "actual_output", "Float"), ("First-Pass Good Output", "first_pass_good", "Float"), ("Good Output %", "good_output_percent", "Percent"), ("Rejected Quantity", "rejected_quantity", "Float"), ("Rework Quantity", "rework_quantity", "Float"), ("Rework %", "rework_percent", "Percent"), ("Usable Scrap", "usable_scrap", "Float"), ("Unusable Scrap", "unusable_scrap", "Float"), ("Scrap %", "scrap_percent", "Percent"), ("Planned Production Hours", "planned_hours", "Float"), ("Available Hours", "available_hours", "Float"), ("Actual Running Hours", "actual_running_hours", "Float"), ("Runtime Overrun Hours", "runtime_overrun_hours", "Float"), ("Downtime Hours", "downtime_hours", "Float"), ("Idle Hours", "idle_hours", "Float"), ("Machine Utilization %", "machine_utilization", "Percent"), ("Actual Production / Running Hour", "actual_per_running_hour", "Float"), ("Ideal Production Rate", "ideal_rate", "Float"), ("Availability %", "availability_percent", "Percent"), ("Performance %", "performance_percent", "Percent"), ("Quality %", "quality_percent", "Percent"), ("OEE %", "oee_percent", "Percent"), ("Completed Jobs", "completed_jobs", "Int"),
	]
	columns = []
	for item in column_defs:
		column = {"label": item[0], "fieldname": item[1], "fieldtype": item[2]}
		if len(item) == 4:
			column["options"] = item[3]
		columns.append(column)
	return columns, list(groups.values()), "<br>".join(sorted(set(warnings))) if warnings else None, None, None
