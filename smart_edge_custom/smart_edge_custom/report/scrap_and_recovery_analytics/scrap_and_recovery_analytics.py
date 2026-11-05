"""Read-only Scrap & Recovery analytics using authoritative Phase 6 records."""

from collections import defaultdict
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate_filters(filters)
	frappe.has_permission("SmartEdge Production Job", "read", throw=True)
	jobs = _get_jobs(filters)
	rows, warnings = _build_detail_rows(jobs, filters)
	data = aggregate_rows(rows, filters.group_by)
	columns = _columns(filters.group_by)
	summary = _summary(rows)
	message = "<br>".join(frappe.utils.escape_html(message) for message in warnings) if warnings else None
	return columns, data, message, _chart(data, filters.group_by), summary


def safe_percentage(numerator, denominator, complete=True):
	if not complete or flt(denominator) <= 0:
		return None
	return flt(flt(numerator) * 100 / flt(denominator), 2)


def stock_quantity_to_kg(quantity, factor):
	return None if not factor or flt(factor) <= 0 else flt(flt(quantity) / flt(factor), 6)


def shift_production_date(actual_start, shift_start=None, shift_end=None):
	"""Assign after-midnight work to the prior date for an explicitly overnight shift."""
	if not actual_start:
		return None
	value = actual_start if isinstance(actual_start, datetime) else frappe.utils.get_datetime(actual_start)
	def seconds(value):
		if isinstance(value, timedelta):
			return value.total_seconds()
		return value.hour * 3600 + value.minute * 60 + value.second
	if shift_start is not None and shift_end is not None and seconds(shift_start) > seconds(shift_end) and seconds(value.time()) < seconds(shift_end):
		return value.date() - timedelta(days=1)
	return value.date()


def aggregate_rows(rows, group_by):
	key_field = {
		"Workstation": "workstation", "Product": "item_code", "Shift": "shift",
		"Operation": "operation", "Date": "production_date", "Production Job": "production_job",
	}.get(group_by, "workstation")
	groups = {}
	for row in rows:
		key_value = row.get(key_field) or (_("Unassigned") if key_field in {"workstation", "shift", "operation"} else _("Unknown"))
		key = (key_value, row.get("production_date")) if group_by == "Shift" else key_value
		if group_by == "Production Job":
			groups[key] = frappe._dict(row)
			continue
		group = groups.setdefault(key, frappe._dict(
			group_value=key_value, production_date=row.get("production_date") if group_by == "Shift" else None,
			item_name=row.get("item_name") if group_by == "Product" else None,
			production_jobs=0, production_quantity_kg=0,
			usable_scrap_kg=0, unusable_scrap_kg=0, total_scrap_kg=0,
			grinder_input_kg=0, grinder_output_kg=0, pulverizer_input_kg=0, pulverizer_output_kg=0,
			recovered_opening_kg=0, recovered_newly_released_kg=0,
			recovered_material_produced_kg=0, recovered_material_reused_kg=0,
			recovered_available_for_reuse_kg=0, recovered_closing_available_kg=0,
			scrap_stored_kg=0, scrap_disposed_kg=0, scrap_pending_disposal_kg=0,
			_scrap_complete=True, _recovery_complete=True,
		))
		group.production_jobs += 1
		for fieldname in (
			"production_quantity_kg", "usable_scrap_kg", "unusable_scrap_kg",
			"grinder_input_kg", "grinder_output_kg", "pulverizer_input_kg", "pulverizer_output_kg",
			"recovered_opening_kg", "recovered_newly_released_kg",
			"recovered_material_produced_kg", "recovered_material_reused_kg",
			"recovered_available_for_reuse_kg", "recovered_closing_available_kg",
			"scrap_stored_kg", "scrap_disposed_kg", "scrap_pending_disposal_kg",
		):
			group[fieldname] += flt(row.get(fieldname))
		group._scrap_complete = group._scrap_complete and bool(row.get("scrap_kg_complete"))
		group._recovery_complete = group._recovery_complete and bool(row.get("recovery_kg_complete"))
	for group in groups.values():
		if group_by == "Production Job":
			continue
		group.total_scrap_kg = (group.usable_scrap_kg + group.unusable_scrap_kg) if group._scrap_complete else None
		group.recovery_percent = safe_percentage(group.usable_scrap_kg, group.total_scrap_kg, group._scrap_complete)
		group.unusable_scrap_percent = safe_percentage(group.unusable_scrap_kg, group.total_scrap_kg, group._scrap_complete)
		group.recovery_utilization_percent = safe_percentage(
			group.recovered_material_reused_kg,
			group.recovered_available_for_reuse_kg,
			group._recovery_complete,
		)
	return list(groups.values())


def _validate_filters(filters):
	if not filters.get("from_date") or not filters.get("to_date") or not filters.get("company"):
		frappe.throw(_("From Date, To Date, and Company are required."))
	filters.from_date, filters.to_date = getdate(filters.from_date), getdate(filters.to_date)
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date."))
	if (filters.to_date - filters.from_date).days > 366:
		frappe.throw(_("Select a reporting period of 366 days or less."))
	if not frappe.has_permission("Company", "read", doc=frappe.get_doc("Company", filters.company)):
		frappe.throw(_("You do not have permission to report on Company {0}.").format(filters.company), frappe.PermissionError)


def _get_jobs(filters):
	# Query one extra day, then apply the exact Shift production date after
	# resolving overnight Shift Type boundaries.
	job_filters = [
		["SmartEdge Production Job", "status", "=", "Completed"],
		["SmartEdge Production Job", "company", "=", filters.company],
		["SmartEdge Production Job", "actual_start", ">=", filters.from_date],
		["SmartEdge Production Job", "actual_start", "<", filters.to_date + timedelta(days=2)],
	]
	for fieldname in ("workstation", "shift", "operation"):
		if filters.get(fieldname):
			job_filters.append(["SmartEdge Production Job", fieldname, "=", filters[fieldname]])
	if filters.scrap_type == "Usable Scrap":
		job_filters.append(["SmartEdge Production Job", "usable_scrap", ">", 0])
	elif filters.scrap_type == "Unusable Scrap":
		job_filters.append(["SmartEdge Production Job", "unusable_scrap", ">", 0])
	if filters.batch_or_lot:
		job_names = _jobs_for_batch_or_lot(filters.batch_or_lot)
		job_filters.append(["SmartEdge Production Job", "name", "in", job_names or [""]])
	return frappe.get_list(
		"SmartEdge Production Job", filters=job_filters,
		fields=["name", "company", "plan_entry", "final_eb_bom_generator", "operation", "workstation",
			"operator_employee", "shift", "actual_start", "actual_end", "actual_qty_kg", "usable_scrap", "unusable_scrap"],
		order_by="actual_end asc, name asc", limit_page_length=0,
	)


def _jobs_for_batch_or_lot(value):
	jobs = set(frappe.get_list("SmartEdge Production Lot", filters={"name": value}, pluck="production_job", limit_page_length=0))
	for batch in frappe.get_list("Batch", filters={"name": value}, fields=[
		"custom_operation_lot", "custom_smartedge_source_production_job",
	], limit_page_length=1):
		if batch.custom_smartedge_source_production_job:
			jobs.add(batch.custom_smartedge_source_production_job)
		if batch.custom_operation_lot:
			jobs.update(frappe.get_list("SmartEdge Production Lot", filters={"name": batch.custom_operation_lot}, pluck="production_job", limit_page_length=1))
	for doctype in ("SmartEdge Usable Scrap Lot", "SmartEdge Unusable Scrap Lot"):
		for fieldname in ("source_batch", "production_lot", "scrap_batch"):
			jobs.update(frappe.get_list(doctype, filters={fieldname: value}, pluck="production_job", limit_page_length=0))
	recovery_lots = frappe.get_list("SmartEdge Scrap Recovery", filters=[
		["SmartEdge Scrap Recovery", "ground_material_batch", "=", value],
	], pluck="usable_scrap_lot", limit_page_length=0)
	recovery_lots += frappe.get_list("SmartEdge Scrap Recovery", filters={"recovered_material_batch": value}, pluck="usable_scrap_lot", limit_page_length=0)
	if recovery_lots:
		jobs.update(frappe.get_list("SmartEdge Usable Scrap Lot", filters={"name": ["in", recovery_lots]}, pluck="production_job", limit_page_length=0))
	return list(jobs)


def _build_detail_rows(jobs, filters):
	if not jobs:
		return [], []
	job_names = [row.name for row in jobs]
	plan_names = list({row.plan_entry for row in jobs if row.plan_entry})
	generator_names = list({row.final_eb_bom_generator for row in jobs if row.final_eb_bom_generator})
	plans = ({row.name: row for row in frappe.get_list("SmartEdge Production Plan Entry", filters={"name": ["in", plan_names]},
		fields=["name", "plan_type", "material_item"], limit_page_length=0)} if plan_names else {})
	generators = ({row.name: row for row in frappe.get_list("Final EB BOM Generator", filters={"name": ["in", generator_names]},
		fields=["name", "finished_item"], limit_page_length=0)} if generator_names else {})
	products = {}
	for job in jobs:
		plan = plans.get(job.plan_entry)
		products[job.name] = plan.material_item if plan and plan.plan_type == "Required Material" else (
			generators.get(job.final_eb_bom_generator).finished_item if generators.get(job.final_eb_bom_generator) else None
		)
	if filters.finished_item:
		jobs = [job for job in jobs if products.get(job.name) == filters.finished_item]
		job_names = [row.name for row in jobs]
	if not jobs:
		return [], []
	item_names = list({item for item in products.values() if item})
	items = ({row.name: row for row in frappe.get_list("Item", filters={"name": ["in", item_names]},
		fields=["name", "item_name"], limit_page_length=0)} if item_names else {})
	configs = ({row.source_item: row for row in frappe.get_list("SmartEdge Recovery Material",
		filters={"source_item": ["in", item_names], "disabled": 0},
		fields=["name", "source_item", "usable_scrap_item", "unusable_scrap_item", "qc_required"], limit_page_length=0)} if item_names else {})
	usable_lots = {row.production_job: row for row in frappe.get_list("SmartEdge Usable Scrap Lot",
		filters={"production_job": ["in", job_names], "status": ["!=", "Cancelled"]}, fields=["name", "production_job", "production_lot", "source_batch", "scrap_item", "scrap_batch", "stock_entry"], limit_page_length=0)}
	unusable_lots = {row.production_job: row for row in frappe.get_list("SmartEdge Unusable Scrap Lot",
		filters={"production_job": ["in", job_names], "status": ["!=", "Cancelled"]}, fields=["name", "production_job", "production_lot", "source_batch", "scrap_item", "quantity", "stored_quantity", "available_quantity", "storage_stock_entry"], limit_page_length=0)}
	recovery_by_job, recovery_items = _recovery_metrics(usable_lots, configs, filters.to_date)
	stored_by_job, disposal_items = _unusable_metrics(unusable_lots, filters.to_date)
	report_items = set(item_names) | recovery_items | disposal_items
	factors, uoms = _kg_factors(report_items)
	shift_names = list({job.shift for job in jobs if job.shift})
	shifts = {row.name: row for row in frappe.get_list("Shift Type", filters={"name": ["in", shift_names]},
		fields=["name", "start_time", "end_time"], limit_page_length=0)} if shift_names else {}
	warnings = set()
	result = []
	for job in jobs:
		item_code = products.get(job.name)
		config = configs.get(item_code)
		usable_item = config.usable_scrap_item if config else None
		unusable_item = config.unusable_scrap_item if config else None
		usable_kg = stock_quantity_to_kg(job.usable_scrap, factors.get(usable_item)) if flt(job.usable_scrap) else 0
		unusable_kg = stock_quantity_to_kg(job.unusable_scrap, factors.get(unusable_item)) if flt(job.unusable_scrap) else 0
		scrap_complete = (not flt(job.usable_scrap) or usable_kg is not None) and (not flt(job.unusable_scrap) or unusable_kg is not None)
		if not scrap_complete:
			warnings.add(_("Some scrap quantities have no authoritative Item conversion to Kg; affected percentages display N/A."))
		recovery = recovery_by_job.get(job.name, frappe._dict())
		stored = stored_by_job.get(job.name, frappe._dict())
		shift = shifts.get(job.shift)
		production_date = shift_production_date(job.actual_start or job.actual_end, shift.start_time if shift else None, shift.end_time if shift else None)
		if not production_date or not (filters.from_date <= production_date <= filters.to_date):
			continue
		usable_lot = usable_lots.get(job.name)
		unusable_lot = unusable_lots.get(job.name)
		row = frappe._dict(
			production_job=job.name, production_date=production_date, item_code=item_code,
			item_name=items.get(item_code).item_name if items.get(item_code) else item_code,
			operation=job.operation or _("Unassigned"), workstation=job.workstation or _("Unassigned"),
			shift=job.shift or _("Unassigned"), employee=job.operator_employee,
			production_quantity_kg=flt(job.actual_qty_kg), usable_scrap_kg=usable_kg,
			unusable_scrap_kg=unusable_kg, scrap_kg_complete=scrap_complete,
			usable_scrap_original=f"{flt(job.usable_scrap, 6)} {uoms.get(usable_item) or ''}".strip(),
			unusable_scrap_original=f"{flt(job.unusable_scrap, 6)} {uoms.get(unusable_item) or ''}".strip(),
			production_lot=(usable_lot or unusable_lot).production_lot if (usable_lot or unusable_lot) else None,
			source_batch=(usable_lot or unusable_lot).source_batch if (usable_lot or unusable_lot) else None,
			usable_scrap_lot=usable_lot.name if usable_lot else None,
			unusable_scrap_lot=unusable_lot.name if unusable_lot else None,
			**recovery, **stored,
		)
		row.total_scrap_kg = (flt(usable_kg) + flt(unusable_kg)) if scrap_complete else None
		row.recovery_percent = safe_percentage(usable_kg, row.total_scrap_kg, scrap_complete)
		row.unusable_scrap_percent = safe_percentage(unusable_kg, row.total_scrap_kg, scrap_complete)
		row.recovery_utilization_percent = safe_percentage(row.get("recovered_material_reused_kg"), row.get("recovered_available_for_reuse_kg"), row.get("recovery_kg_complete", True))
		result.append(row)
	return result, sorted(warnings)


def _kg_factors(item_codes):
	item_codes = list({item for item in item_codes if item})
	if not item_codes:
		return {}, {}
	items = frappe.get_list("Item", filters={"name": ["in", item_codes]}, fields=["name", "stock_uom"], limit_page_length=0)
	factors, uoms = {}, {row.name: row.stock_uom for row in items}
	for row in items:
		if row.stock_uom == "Kg":
			factors[row.name] = 1
	conversions = frappe.get_all("UOM Conversion Detail", filters={"parent": ["in", item_codes], "uom": "Kg"},
		fields=["parent", "conversion_factor"], limit_page_length=0)
	for row in conversions:
		if flt(row.conversion_factor) > 0:
			factors[row.parent] = flt(row.conversion_factor)
	return factors, uoms


def _recovery_metrics(usable_lots, configs, to_date):
	if not usable_lots:
		return {}, set()
	lot_to_job = {lot.name: job for job, lot in usable_lots.items()}
	recoveries = frappe.get_list("SmartEdge Scrap Recovery", filters={
		"usable_scrap_lot": ["in", list(lot_to_job)], "status": "Recovered",
	}, fields=["name", "usable_scrap_lot", "material_configuration", "ground_material_item", "grinder_input_quantity", "ground_material_quantity",
		"pulverizer_input_quantity", "recovered_material_item", "recovered_material_quantity", "pulverizer_stock_entry", "quality_inspection", "qc_required"], limit_page_length=0)
	stock_entries = _submitted_entries([row.pulverizer_stock_entry for row in recoveries], to_date)
	inspection_names = [row.quality_inspection for row in recoveries if row.quality_inspection]
	accepted = set(frappe.get_list("Quality Inspection", filters={"name": ["in", inspection_names], "docstatus": 1,
		"status": "Accepted", "modified": ["<", to_date + timedelta(days=1)]}, pluck="name", limit_page_length=0)) if inspection_names else set()
	valid = [row for row in recoveries if row.pulverizer_stock_entry in stock_entries]
	allocations = frappe.get_list("SmartEdge Recovered Material Allocation", filters={
		"recovery": ["in", [row.name for row in valid] or [""]], "status": "Consumed",
	}, fields=["recovery", "allocated_quantity", "recovered_material_item", "consumption_stock_entry"], limit_page_length=0)
	consumption_entries = _submitted_entries([row.consumption_stock_entry for row in allocations], to_date)
	allocations_by_recovery = defaultdict(float)
	for row in allocations:
		if row.consumption_stock_entry in consumption_entries:
			allocations_by_recovery[row.recovery] += flt(row.allocated_quantity)
	result = defaultdict(lambda: frappe._dict(
		grinder_input_kg=0, grinder_output_kg=0, pulverizer_input_kg=0,
		pulverizer_output_kg=0, recovered_opening_kg=0, recovered_newly_released_kg=0,
		recovered_material_produced_kg=0, recovered_material_reused_kg=0,
		recovered_available_for_reuse_kg=0, recovered_closing_available_kg=0,
		recovery_kg_complete=True, recovery=None, recovery_stock_entry=None,
	))
	configs_by_name = {config.name: config for config in configs.values()}
	item_codes = ({row.ground_material_item for row in valid} | {row.recovered_material_item for row in valid}
		| {configs_by_name[row.material_configuration].usable_scrap_item for row in valid if row.material_configuration in configs_by_name})
	factors, _uoms = _kg_factors(item_codes)
	for row in valid:
		job = lot_to_job[row.usable_scrap_lot]
		metric = result[job]
		ground_factor, recovered_factor = factors.get(row.ground_material_item), factors.get(row.recovered_material_item)
		values = {
			"grinder_output_kg": stock_quantity_to_kg(row.ground_material_quantity, ground_factor),
			"pulverizer_input_kg": stock_quantity_to_kg(row.pulverizer_input_quantity, ground_factor),
			"recovered_material_produced_kg": stock_quantity_to_kg(row.recovered_material_quantity, recovered_factor),
			"pulverizer_output_kg": stock_quantity_to_kg(row.recovered_material_quantity, recovered_factor),
			"recovered_material_reused_kg": stock_quantity_to_kg(allocations_by_recovery[row.name], recovered_factor),
		}
		# Grinder input uses the usable-scrap Item factor, resolved with the recovery config below.
		config = configs_by_name.get(row.material_configuration)
		usable_factor = factors.get(config.usable_scrap_item) if config else None
		values["grinder_input_kg"] = stock_quantity_to_kg(row.grinder_input_quantity, usable_factor)
		if any(value is None for value in values.values()):
			metric.recovery_kg_complete = False
		for fieldname, value in values.items():
			metric[fieldname] += flt(value)
		released = not cint(row.qc_required) or row.quality_inspection in accepted
		if released:
			metric.recovered_available_for_reuse_kg += flt(values["recovered_material_produced_kg"])
			metric.recovered_newly_released_kg += flt(values["recovered_material_produced_kg"])
		metric.recovered_closing_available_kg = max(metric.recovered_available_for_reuse_kg - metric.recovered_material_reused_kg, 0)
		metric.recovery = metric.recovery or row.name
		metric.recovery_stock_entry = metric.recovery_stock_entry or row.pulverizer_stock_entry
	return dict(result), item_codes


def _submitted_entries(names, to_date):
	names = list({name for name in names if name})
	if not names:
		return set()
	return set(frappe.get_list("Stock Entry", filters={"name": ["in", names], "docstatus": 1, "posting_date": ["<=", to_date]}, pluck="name", limit_page_length=0))


def _unusable_metrics(lots, to_date):
	if not lots:
		return {}, set()
	storage_entries = _submitted_entries([row.storage_stock_entry for row in lots.values()], to_date)
	disposals = frappe.get_list("Stock Entry", filters={
		"custom_smartedge_unusable_scrap_lot": ["in", [row.name for row in lots.values()]],
		"custom_smartedge_scrap_movement_type": "Unusable Scrap Disposal", "docstatus": 1,
		"posting_date": ["<=", to_date],
	}, fields=["name", "custom_smartedge_unusable_scrap_lot"], limit_page_length=0)
	disposal_by_lot = defaultdict(float)
	if disposals:
		disposal_lots = {row.name: row.custom_smartedge_unusable_scrap_lot for row in disposals}
		for row in frappe.get_all("Stock Entry Detail", filters={"parent": ["in", [d.name for d in disposals]]},
			fields=["parent", "transfer_qty"], limit_page_length=0):
			lot_name = disposal_lots[row.parent]
			disposal_by_lot[lot_name] += flt(row.transfer_qty)
	result, items = {}, {row.scrap_item for row in lots.values() if row.scrap_item}
	factors, _uoms = _kg_factors(items)
	for job, lot in lots.items():
		factor = factors.get(lot.scrap_item)
		stored_qty = lot.quantity if lot.storage_stock_entry in storage_entries else 0
		disposed_qty = disposal_by_lot[lot.name]
		stored_kg, disposed_kg = stock_quantity_to_kg(stored_qty, factor), stock_quantity_to_kg(disposed_qty, factor)
		result[job] = frappe._dict(
			scrap_stored_kg=stored_kg, scrap_disposed_kg=disposed_kg,
			scrap_pending_disposal_kg=(max(flt(stored_kg) - flt(disposed_kg), 0) if stored_kg is not None and disposed_kg is not None else None),
			unusable_storage_stock_entry=lot.storage_stock_entry,
		)
	return result, items


def _summary(rows):
	totals = aggregate_rows(rows, "Workstation")
	combined = frappe._dict()
	for row in totals:
		for fieldname, value in row.items():
			if fieldname not in {"group_value", "recovery_percent", "unusable_scrap_percent", "recovery_utilization_percent"}:
				combined[fieldname] = flt(combined.get(fieldname)) + flt(value)
	scrap_complete = all(row.get("scrap_kg_complete") for row in rows)
	recovery_complete = all(row.get("recovery_kg_complete", True) for row in rows)
	total_scrap = (flt(combined.usable_scrap_kg) + flt(combined.unusable_scrap_kg)) if scrap_complete else None
	utilization = safe_percentage(combined.recovered_material_reused_kg, combined.recovered_available_for_reuse_kg, recovery_complete)
	recovery_percent = safe_percentage(combined.usable_scrap_kg, total_scrap, scrap_complete) if total_scrap else None
	unusable_percent = safe_percentage(combined.unusable_scrap_kg, total_scrap, scrap_complete) if total_scrap else None
	def summary_value(value, label, datatype):
		return {"value": value if value is not None else _("N/A"), "label": label,
			"datatype": datatype if value is not None else "Data"}
	return [
		summary_value(recovery_percent, _("Recovery %"), "Percent"),
		summary_value(unusable_percent, _("Unusable Scrap %"), "Percent"),
		summary_value(utilization, _("Recovery Utilization %"), "Percent"),
		{"value": total_scrap if total_scrap is not None else _("N/A"), "label": _("Total Scrap Generated"), "datatype": "Float" if total_scrap is not None else "Data"},
		{"value": combined.usable_scrap_kg, "label": _("Usable Scrap (Kg)"), "datatype": "Float"},
		{"value": combined.unusable_scrap_kg, "label": _("Unusable Scrap (Kg)"), "datatype": "Float"},
		summary_value(combined.recovered_material_reused_kg if recovery_complete else None, _("Recovered Material Reused (Kg)"), "Float"),
	]


def _columns(group_by):
	if group_by == "Production Job":
		return [
			{"fieldname":"production_job","label":_("Production Job"),"fieldtype":"Link","options":"SmartEdge Production Job","width":170},
			{"fieldname":"production_date","label":_("Production Date"),"fieldtype":"Date","width":110},
			{"fieldname":"item_code","label":_("Finished Item / Product"),"fieldtype":"Link","options":"Item","width":150},
			{"fieldname":"operation","label":_("Operation"),"fieldtype":"Link","options":"Operation","width":130},
			{"fieldname":"workstation","label":_("Workstation"),"fieldtype":"Link","options":"Workstation","width":130},
			{"fieldname":"shift","label":_("Shift"),"fieldtype":"Link","options":"Shift Type","width":110},
			{"fieldname":"employee","label":_("Employee"),"fieldtype":"Link","options":"Employee","width":120},
			{"fieldname":"production_lot","label":_("Production Lot"),"fieldtype":"Link","options":"SmartEdge Production Lot","width":140},
			{"fieldname":"source_batch","label":_("Source Batch"),"fieldtype":"Link","options":"Batch","width":130},
			{"fieldname":"usable_scrap_original","label":_("Usable Scrap (Original UOM)"),"fieldtype":"Data","width":165},
			{"fieldname":"unusable_scrap_original","label":_("Unusable Scrap (Original UOM)"),"fieldtype":"Data","width":175},
			*_metric_columns(),
			{"fieldname":"usable_scrap_lot","label":_("Usable Scrap Lot"),"fieldtype":"Link","options":"SmartEdge Usable Scrap Lot","width":140},
			{"fieldname":"unusable_scrap_lot","label":_("Unusable Scrap Lot"),"fieldtype":"Link","options":"SmartEdge Unusable Scrap Lot","width":145},
			{"fieldname":"recovery","label":_("Recovery"),"fieldtype":"Link","options":"SmartEdge Scrap Recovery","width":120},
			{"fieldname":"recovery_stock_entry","label":_("Recovery Stock Entry"),"fieldtype":"Link","options":"Stock Entry","width":150},
			{"fieldname":"unusable_storage_stock_entry","label":_("Storage Stock Entry"),"fieldtype":"Link","options":"Stock Entry","width":150},
		]
	label = {"Workstation": _("Workstation"), "Product": _("Product"), "Shift": _("Shift"), "Operation": _("Operation"), "Date": _("Production Date")}.get(group_by, _("Group"))
	group_type, options = {
		"Workstation": ("Link", "Workstation"), "Product": ("Link", "Item"),
		"Shift": ("Link", "Shift Type"), "Operation": ("Link", "Operation"), "Date": ("Date", None),
	}.get(group_by, ("Data", None))
	columns = [{"fieldname":"group_value","label":label,"fieldtype":group_type,"options":options,"width":180}]
	if group_by == "Product":
		columns.append({"fieldname":"item_name","label":_("Item Name"),"fieldtype":"Data","width":180})
	if group_by == "Shift":
		columns.append({"fieldname":"production_date","label":_("Production Date"),"fieldtype":"Date","width":110})
	return [*columns, {"fieldname":"production_jobs","label":_("Production Jobs"),"fieldtype":"Int","width":110}, *_metric_columns()]


def _metric_columns():
	return [
		{"fieldname":"production_quantity_kg","label":_("Production Qty (Kg)"),"fieldtype":"Float","precision":6,"width":130},
		{"fieldname":"usable_scrap_kg","label":_("Usable Scrap (Kg)"),"fieldtype":"Float","precision":6,"width":125},
		{"fieldname":"unusable_scrap_kg","label":_("Unusable Scrap (Kg)"),"fieldtype":"Float","precision":6,"width":135},
		{"fieldname":"total_scrap_kg","label":_("Total Scrap (Kg)"),"fieldtype":"Float","precision":6,"width":120},
		{"fieldname":"recovery_percent","label":_("Recovery %"),"fieldtype":"Percent","width":95},
		{"fieldname":"unusable_scrap_percent","label":_("Unusable Scrap %"),"fieldtype":"Percent","width":115},
		{"fieldname":"grinder_input_kg","label":_("Grinder Input (Kg)"),"fieldtype":"Float","precision":6,"width":125},
		{"fieldname":"grinder_output_kg","label":_("Grinder Output (Kg)"),"fieldtype":"Float","precision":6,"width":130},
		{"fieldname":"pulverizer_input_kg","label":_("Pulverizer Input (Kg)"),"fieldtype":"Float","precision":6,"width":135},
		{"fieldname":"pulverizer_output_kg","label":_("Pulverizer Output (Kg)"),"fieldtype":"Float","precision":6,"width":140},
		{"fieldname":"recovered_opening_kg","label":_("Opening Recovered Cohort (Kg)"),"fieldtype":"Float","precision":6,"width":175},
		{"fieldname":"recovered_newly_released_kg","label":_("Newly Released Recovery (Kg)"),"fieldtype":"Float","precision":6,"width":175},
		{"fieldname":"recovered_material_produced_kg","label":_("Recovered Produced (Kg)"),"fieldtype":"Float","precision":6,"width":150},
		{"fieldname":"recovered_material_reused_kg","label":_("Recovered Reused (Kg)"),"fieldtype":"Float","precision":6,"width":145},
		{"fieldname":"recovered_available_for_reuse_kg","label":_("Released Recovery Cohort (Kg)"),"fieldtype":"Float","precision":6,"width":170},
		{"fieldname":"recovered_closing_available_kg","label":_("Closing Recovered Stock (Kg)"),"fieldtype":"Float","precision":6,"width":170},
		{"fieldname":"recovery_utilization_percent","label":_("Recovery Utilization %"),"fieldtype":"Percent","width":150},
		{"fieldname":"scrap_stored_kg","label":_("Scrap Stored (Kg)"),"fieldtype":"Float","precision":6,"width":125},
		{"fieldname":"scrap_disposed_kg","label":_("Scrap Disposed (Kg)"),"fieldtype":"Float","precision":6,"width":130},
		{"fieldname":"scrap_pending_disposal_kg","label":_("Pending Disposal (Kg)"),"fieldtype":"Float","precision":6,"width":145},
	]


def _chart(data, group_by):
	if group_by == "Production Job" or not data:
		return None
	return {"data": {"labels": [f"{row.group_value} / {row.production_date}" if group_by == "Shift" else str(row.group_value) for row in data], "datasets": [
		{"name": _("Usable Scrap"), "values": [flt(row.usable_scrap_kg) for row in data]},
		{"name": _("Unusable Scrap"), "values": [flt(row.unusable_scrap_kg) for row in data]},
	]}, "type": "bar", "colors": ["#4CAF50", "#E67E22"]}
