import hashlib
import json
from collections import defaultdict
from functools import lru_cache

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime


MOVEMENT_DOCTYPE = "SmartEdge Batch Genealogy Movement"
ACTIVE = "Active"
CANCELLED = "Cancelled"
MAX_DEPTH = 12
MAX_BATCH_NODES = 250
MAX_MOVEMENTS_PER_LEVEL = 2000

MOVEMENT_FIELDS = [
	"name",
	"stock_entry",
	"stock_entry_detail",
	"serial_and_batch_bundle",
	"direction",
	"batch_no",
	"item_code",
	"quantity",
	"uom",
	"warehouse",
	"is_scrap_or_recovery",
	"manufacturing_stage",
	"company",
	"posting_datetime",
	"work_order",
	"production_job",
	"production_jobs_json",
	"plan_entry",
	"final_eb_bom_generator",
	"operation",
	"operations_json",
	"operation_context_json",
	"workstation",
	"employee",
	"actual_start",
	"actual_end",
	"quality_inspection",
	"good_quantity",
	"rejected_quantity",
	"rework_quantity",
	"usable_scrap",
	"unusable_scrap",
]


def sync_genealogy_from_stock_entry(doc, method=None):
	"""Stock Entry hook: observe submitted manufacturing evidence without changing stock."""
	if doc.purpose != "Manufacture":
		return
	if cint(doc.docstatus) == 2 or method == "on_cancel":
		mark_stock_entry_cancelled(doc.name)
		return
	if cint(doc.docstatus) == 1:
		capture_stock_entry(doc)


def refresh_genealogy_from_production_job(doc, method=None):
	"""Refresh context after a job receives its submitted manufacturing reference."""
	stock_entry = doc.get("manufacturing_stock_entry")
	if not stock_entry or not frappe.db.exists("Stock Entry", {"name": stock_entry, "docstatus": 1}):
		return
	capture_stock_entry(stock_entry)


def capture_stock_entry(stock_entry):
	if isinstance(stock_entry, str):
		stock_entry = frappe.get_doc("Stock Entry", stock_entry)
	if stock_entry.purpose != "Manufacture" or cint(stock_entry.docstatus) != 1:
		return {"created": 0, "updated": 0, "skipped": 0}

	movements = extract_batch_movements(stock_entry)
	context = get_manufacturing_context(stock_entry, movements)
	created = updated = skipped = 0
	active_keys = set()
	for movement in movements:
		if not validate_movement_evidence(movement, stock_entry):
			skipped += 1
			continue
		movement.update(context)
		movement["quality_inspection"] = movement.get("quality_inspection") or context.get(
			"quality_inspection"
		)
		movement["source_status"] = ACTIVE
		movement["allocation_basis"] = "Manufacturing Event"
		movement["source_key"] = make_source_key(
			stock_entry.name,
			movement["stock_entry_detail"],
			movement["batch_no"],
			movement["direction"],
		)
		active_keys.add(movement["source_key"])
		name = frappe.db.exists(MOVEMENT_DOCTYPE, {"source_key": movement["source_key"]})
		if name:
			frappe.db.set_value(MOVEMENT_DOCTYPE, name, movement, update_modified=False)
			updated += 1
		else:
			frappe.get_doc({"doctype": MOVEMENT_DOCTYPE, **movement}).insert(ignore_permissions=True)
			created += 1

	existing = frappe.get_all(
		MOVEMENT_DOCTYPE,
		filters={"stock_entry": stock_entry.name, "source_status": ACTIVE},
		fields=["name", "source_key"],
		limit_page_length=MAX_MOVEMENTS_PER_LEVEL,
	)
	for row in existing:
		if row.source_key not in active_keys:
			frappe.db.set_value(MOVEMENT_DOCTYPE, row.name, "source_status", CANCELLED, update_modified=False)

	return {"created": created, "updated": updated, "skipped": skipped}


def mark_stock_entry_cancelled(stock_entry):
	frappe.db.set_value(
		MOVEMENT_DOCTYPE,
		{"stock_entry": stock_entry, "source_status": ACTIVE},
		"source_status",
		CANCELLED,
		update_modified=False,
	)


def extract_batch_movements(stock_entry, bundle_loader=None):
	"""Return independently evidenced batch movements; never allocate inputs to outputs."""
	bundle_loader = bundle_loader or _load_bundle_entries
	result = []
	for row in stock_entry.get("items") or []:
		direction = get_row_direction(row)
		if not direction:
			continue
		batch_quantities = defaultdict(float)
		bundle_name = row.get("serial_and_batch_bundle")
		if bundle_name:
			for entry in bundle_loader(bundle_name):
				if entry.get("batch_no"):
					batch_quantities[entry.batch_no] += abs(flt(entry.get("qty")))
		elif row.get("batch_no"):
			batch_quantities[row.batch_no] += abs(
				flt(row.get("transfer_qty"))
				or flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1)
			)

		warehouse = row.get("s_warehouse") if direction == "Input" else row.get("t_warehouse")
		for batch_no, quantity in batch_quantities.items():
			if quantity <= 0:
				continue
			result.append(
				{
					"stock_entry": stock_entry.name,
					"stock_entry_detail": row.name,
					"serial_and_batch_bundle": bundle_name,
					"direction": direction,
					"batch_no": batch_no,
					"item_code": row.item_code,
					"quantity": quantity,
					"uom": row.get("stock_uom") or row.get("uom"),
					"warehouse": warehouse,
					"is_scrap_or_recovery": cint(direction == "Scrap / Recovery"),
					"quality_inspection": row.get("quality_inspection"),
				}
			)
	return result


def get_row_direction(row):
	if cint(row.get("is_finished_item")):
		return "Output"
	if cint(row.get("is_legacy_scrap_item")) or (row.get("t_warehouse") and not row.get("s_warehouse")):
		return "Scrap / Recovery"
	if row.get("s_warehouse"):
		return "Input"
	return None


def _load_bundle_entries(bundle_name):
	bundle = frappe.get_doc("Serial and Batch Bundle", bundle_name)
	return bundle.get("entries") or []


def validate_movement_evidence(movement, stock_entry):
	batch_item = frappe.db.get_value("Batch", movement["batch_no"], "item")
	if batch_item != movement["item_code"]:
		_log_skip(stock_entry.name, movement, "batch does not belong to the Stock Entry item")
		return False
	warehouse = movement.get("warehouse")
	if warehouse:
		warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
		if warehouse_company and warehouse_company != stock_entry.company:
			_log_skip(stock_entry.name, movement, "warehouse belongs to another company")
			return False
	return True


def _log_skip(stock_entry, movement, reason):
	frappe.logger("smartedge_batch_genealogy").warning(
		"Skipped unverifiable genealogy movement for %s row %s batch %s: %s",
		stock_entry,
		movement.get("stock_entry_detail"),
		movement.get("batch_no"),
		reason,
	)


def make_source_key(stock_entry, stock_entry_detail, batch_no, direction):
	raw = "|".join(str(value or "") for value in (stock_entry, stock_entry_detail, batch_no, direction))
	return hashlib.sha256(raw.encode()).hexdigest()


def get_manufacturing_context(stock_entry, movements=None):
	plan_entries = _get_plan_entries(stock_entry)
	jobs = _get_production_jobs(stock_entry.name, plan_entries)
	operation_jobs = _get_route_operation_jobs(stock_entry, jobs)
	primary_plan = plan_entries[0] if plan_entries else frappe._dict()
	primary_job = jobs[0] if jobs else frappe._dict()
	operations = _unique([job.operation for job in operation_jobs] + [row.operation for row in plan_entries])
	production_jobs = _unique([job.name for job in operation_jobs])
	primary_operation = primary_job.get("operation") or primary_plan.get("operation")
	operation_context = build_operation_context(
		operation_jobs, stock_entry, movements or [], jobs
	)
	stage = get_manufacturing_stage(
		stock_entry, plan_entries, [primary_operation] if primary_operation else operations
	)
	posting_datetime = get_datetime(f"{stock_entry.posting_date} {stock_entry.posting_time or '00:00:00'}")

	return {
		"manufacturing_stage": stage,
		"company": stock_entry.company,
		"posting_datetime": posting_datetime,
		"work_order": stock_entry.get("work_order") or primary_plan.get("work_order"),
		"production_job": primary_job.get("name"),
		"production_jobs_json": json.dumps(production_jobs),
		"plan_entry": primary_job.get("plan_entry") or primary_plan.get("name"),
		"final_eb_bom_generator": primary_job.get("final_eb_bom_generator")
		or primary_plan.get("final_eb_bom_generator"),
		"operation": primary_operation,
		"operations_json": json.dumps(operations),
		"operation_context_json": json.dumps(operation_context, default=str),
		"workstation": primary_job.get("workstation") or primary_plan.get("workstation"),
		"employee": primary_job.get("operator_employee"),
		"actual_start": min((job.actual_start for job in operation_jobs if job.actual_start), default=None),
		"actual_end": max((job.actual_end for job in operation_jobs if job.actual_end), default=None),
		"good_quantity": sum(flt(job.good_qty) for job in jobs),
		"rejected_quantity": sum(flt(job.rejected_qty) for job in jobs),
		"rework_quantity": sum(flt(job.rework_qty) for job in jobs),
		"usable_scrap": sum(flt(job.usable_scrap) for job in jobs),
		"unusable_scrap": sum(flt(job.unusable_scrap) for job in jobs),
	}


def _get_plan_entries(stock_entry):
	fields = [
		"name", "work_order", "production_job", "final_eb_bom_generator", "plan_type",
		"material_type", "operation", "workstation", "planning_category",
	]
	rows = []
	if stock_entry.get("work_order"):
		rows.extend(
			frappe.get_all(
				"SmartEdge Production Plan Entry",
				filters={"work_order": stock_entry.work_order, "status": ["!=", "Cancelled"]},
				fields=fields,
				order_by="modified desc",
				limit_page_length=20,
			)
		)
	rows.extend(
		frappe.get_all(
			"SmartEdge Production Plan Entry",
			filters={"manufacture_stock_entry": stock_entry.name, "status": ["!=", "Cancelled"]},
			fields=fields,
			order_by="modified desc",
			limit_page_length=20,
		)
	)
	return _unique_rows(rows)


def _get_production_jobs(stock_entry, plan_entries):
	fields = [
		"name", "plan_entry", "final_eb_bom_generator", "operation", "workstation",
		"operator_employee", "actual_start", "actual_end", "good_qty", "rejected_qty",
		"rework_qty", "usable_scrap", "unusable_scrap", "actual_qty_meter", "actual_qty_kg",
	]
	rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={"manufacturing_stock_entry": stock_entry, "status": ["!=", "Cancelled"]},
		fields=fields,
		order_by="actual_end desc, modified desc",
		limit_page_length=100,
	)
	plan_names = [row.name for row in plan_entries]
	if plan_names:
		rows.extend(
			frappe.get_all(
				"SmartEdge Production Job",
				filters={"plan_entry": ["in", plan_names], "status": ["!=", "Cancelled"]},
				fields=fields,
				order_by="actual_end desc, modified desc",
				limit_page_length=100,
			)
		)
	return _unique_rows(rows)


def _get_route_operation_jobs(stock_entry, directly_related_jobs):
	"""Include non-stock route operations as execution context, never as invented batch movement."""
	if stock_entry.get("work_order") or not directly_related_jobs:
		return _annotate_job_plan_context(directly_related_jobs)
	productions = _unique([job.final_eb_bom_generator for job in directly_related_jobs])
	if len(productions) != 1:
		return _annotate_job_plan_context(directly_related_jobs)
	if (
		frappe.db.get_value(
			"Final EB BOM Generator", productions[0], "final_manufacturing_stock_entry"
		)
		!= stock_entry.name
	):
		return _annotate_job_plan_context(directly_related_jobs)
	fields = [
		"name", "plan_entry", "final_eb_bom_generator", "operation", "workstation",
		"operator_employee", "actual_start", "actual_end", "actual_qty_meter", "actual_qty_kg",
		"good_qty", "rejected_qty", "rework_qty", "usable_scrap", "unusable_scrap",
	]
	rows = frappe.get_all(
		"SmartEdge Production Job",
		filters={"final_eb_bom_generator": productions[0], "status": "Completed"},
		fields=fields,
		order_by="actual_start asc, creation asc",
		limit_page_length=100,
	)
	rows = _annotate_job_plan_context(_unique_rows(rows))
	route_rows = [row for row in rows if row.get("plan_type") != "Required Material"]
	return route_rows or _annotate_job_plan_context(directly_related_jobs)


def _annotate_job_plan_context(jobs):
	plan_names = _unique([job.get("plan_entry") for job in jobs])
	if not plan_names:
		return jobs
	plans = {
		row.name: row
		for row in frappe.get_all(
			"SmartEdge Production Plan Entry",
			filters={"name": ["in", plan_names]},
			fields=[
				"name",
				"plan_type",
				"planning_category",
				"sequence",
				"operation_row",
				"quantity_uom",
			],
			limit_page_length=len(plan_names),
		)
	}
	for job in jobs:
		plan = plans.get(job.get("plan_entry")) or frappe._dict()
		for fieldname in (
			"plan_type",
			"planning_category",
			"sequence",
			"operation_row",
			"quantity_uom",
		):
			job[fieldname] = plan.get(fieldname)
	return jobs


def build_operation_context(operation_jobs, stock_entry, movements, directly_related_jobs=None):
	"""Attach physical evidence to operations without inventing intermediate stock movement."""
	if not operation_jobs:
		return []
	direct_names = {job.name for job in directly_related_jobs or []}
	consumption_job = next(
		(
			job
			for job in operation_jobs
			if job.get("planning_category") == "Extrusion"
			or "extrusion" in (job.get("operation") or "").lower()
		),
		operation_jobs[0],
	)
	output_job = next(
		(job for job in operation_jobs if job.name in direct_names),
		operation_jobs[-1],
	)
	if stock_entry.get("work_order"):
		consumption_job = output_job = next(
			(job for job in operation_jobs if job.name in direct_names), operation_jobs[0]
		)

	inputs = [_serialize_movement(row) for row in movements if row.get("direction") == "Input"]
	outputs = [_serialize_movement(row) for row in movements if row.get("direction") == "Output"]
	input_keys = {(row.get("batch_no"), row.get("item_code")) for row in inputs}
	has_new_output_batch = any(
		(row.get("batch_no"), row.get("item_code")) not in input_keys for row in outputs
	)
	result = []
	for job in operation_jobs:
		row = _serialize_job_context(job)
		row.update(
			{
				"related_stock_entry": stock_entry.name,
				"input_batches": inputs if job.name == consumption_job.name else [],
				"output_batches": outputs if job.name == output_job.name else [],
				"physical_batch_transition": cint(
					bool(has_new_output_batch and consumption_job.name == output_job.name == job.name)
				),
				"evidence_basis": (
					"Direct submitted operation Stock Entry"
					if consumption_job.name == output_job.name == job.name
					else "Final manufacturing event; quantities are not allocated between Production Jobs"
				),
				"tracking_mode": (
					"Physical Batch Movement"
					if has_new_output_batch and consumption_job.name == output_job.name == job.name
					else "Submitted Operation Movement — Same Physical Batch"
					if consumption_job.name == output_job.name == job.name and (inputs or outputs)
					else "Posted Input Consumption"
					if job.name == consumption_job.name and inputs
					else "Posted Output Receipt"
					if job.name == output_job.name and outputs
					else "Operation History — Same Physical Identity"
				),
			}
		)
		result.append(row)
	return result


def _serialize_movement(row):
	return {
		"batch_no": row.get("batch_no"),
		"item_code": row.get("item_code"),
		"quantity": row.get("quantity"),
		"uom": row.get("uom"),
		"warehouse": row.get("warehouse"),
		"quality_inspection": row.get("quality_inspection"),
	}


def _serialize_job_context(job):
	return {
		"production_job": job.name,
		"operation": job.operation,
		"planning_category": job.get("planning_category"),
		"sequence": job.get("sequence"),
		"plan_type": job.get("plan_type"),
		"quantity_uom": job.get("quantity_uom"),
		"workstation": job.workstation,
		"employee": job.operator_employee,
		"actual_start": job.actual_start,
		"actual_end": job.actual_end,
		"actual_qty_meter": job.get("actual_qty_meter"),
		"actual_qty_kg": job.get("actual_qty_kg"),
		"good_quantity": job.good_qty,
		"rejected_quantity": job.rejected_qty,
		"rework_quantity": job.rework_qty,
		"usable_scrap": job.usable_scrap,
		"unusable_scrap": job.unusable_scrap,
	}


def get_manufacturing_stage(stock_entry, plan_entries, operations):
	material_types = _unique([row.material_type for row in plan_entries if row.get("material_type")])
	if material_types:
		return material_types[0]
	categories = _unique([row.planning_category for row in plan_entries if row.get("planning_category")])
	if categories:
		return categories[0]
	operation_text = " ".join(operations).lower()
	for keyword, stage in (
		("extrusion", "Extrusion"), ("slitting", "Slitting"), ("printing", "Printing"),
		("packing", "Packing"), ("quality", "QC"), (" qc", "QC"), ("mix", "Mixing"),
	):
		if keyword in operation_text:
			return stage
	finished_items = [row.item_code for row in stock_entry.get("items") or [] if row.get("is_finished_item")]
	if finished_items:
		item_group = frappe.db.get_value("Item", finished_items[0], "item_group")
		if item_group == "Compounds":
			return "Compound"
		if item_group == "Base Colour":
			return "Base Colour"
	return "Manufacturing"


def _unique(values):
	return list(dict.fromkeys(value for value in values if value))


def _unique_rows(rows):
	return list({row.name: row for row in rows}.values())


@frappe.whitelist()
def get_genealogy(batch_no, direction="Backward", filters=None, max_depth=MAX_DEPTH):
	if direction not in {"Backward", "Forward"}:
		frappe.throw(_("Trace Direction must be Backward or Forward."))
	batch = frappe.get_doc("Batch", batch_no)
	batch.check_permission("read")
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	max_depth = min(max(cint(max_depth), 1), MAX_DEPTH)
	graph, discovered, truncated = build_genealogy_graph(
		batch_no, batch.item, direction, filters, max_depth
	)
	details = get_batch_details(discovered)
	result = {
		"direction": direction,
		"root": build_genealogy_tree((batch_no, batch.item), graph, details, set(), [0]),
		"batch_count": len(discovered),
		"truncated": truncated,
	}
	if direction == "Forward" and frappe.db.exists("DocType", "SmartEdge WIP Ledger Entry"):
		from smart_edge_custom.manufacturing.production_lot import ACTIVE as LOT_ACTIVE
		from smart_edge_custom.manufacturing.production_lot import build_lot_genealogy_node

		batch_names = list({key[0] for key in discovered})
		links = frappe.get_all(
			"SmartEdge WIP Ledger Entry",
			filters={
				"status": LOT_ACTIVE,
				"source_stock_batch": ["in", batch_names],
				"destination_production_lot": ["is", "set"],
			},
			fields=["source_stock_batch", "destination_production_lot"],
			limit_page_length=MAX_BATCH_NODES,
		)
		links = [
			row for row in links
			if safe_link("SmartEdge Production Lot", row.destination_production_lot)
		]
		visited_lots = set()
		result["production_lot_roots"] = [
			{
				"attached_to_batch": row.source_stock_batch,
				**build_lot_genealogy_node(row.destination_production_lot, direction, visited_lots, 0),
			}
			for row in links
			if row.destination_production_lot not in visited_lots
		]
		result["production_lot_count"] = len(visited_lots)
	return result


def build_genealogy_graph(root_batch, root_item, direction, filters, max_depth):
	anchor_direction = "Output" if direction == "Backward" else "Input"
	target_direction = "Input" if direction == "Backward" else "Output"
	frontier = {(root_batch, root_item)}
	discovered = set(frontier)
	graph = defaultdict(list)
	seen_events = set()
	truncated = False

	for _depth in range(max_depth):
		if not frontier or len(discovered) >= MAX_BATCH_NODES:
			truncated = bool(frontier)
			break
		batch_names = [key[0] for key in frontier]
		query_filters = {"batch_no": ["in", batch_names], "direction": anchor_direction, "source_status": ACTIVE}
		apply_event_filters(query_filters, filters)
		anchors = frappe.get_list(
			MOVEMENT_DOCTYPE,
			filters=query_filters,
			fields=MOVEMENT_FIELDS,
			limit_page_length=MAX_MOVEMENTS_PER_LEVEL,
		)
		anchors = [row for row in anchors if (row.batch_no, row.item_code) in frontier]
		event_names = _unique([row.stock_entry for row in anchors if row.stock_entry not in seen_events])
		allowed_events = {name for name in event_names if can_read_stock_entry(name)}
		anchors = [row for row in anchors if row.stock_entry in allowed_events]
		if not allowed_events:
			break
		movements = frappe.get_list(
			MOVEMENT_DOCTYPE,
			filters={"stock_entry": ["in", list(allowed_events)], "source_status": ACTIVE},
			fields=MOVEMENT_FIELDS,
			limit_page_length=MAX_MOVEMENTS_PER_LEVEL,
		)
		by_event = defaultdict(list)
		for movement in movements:
			by_event[movement.stock_entry].append(movement)

		next_frontier = set()
		for anchor in anchors:
			event_key = (anchor.batch_no, anchor.item_code, anchor.stock_entry)
			if event_key in seen_events:
				continue
			seen_events.add(event_key)
			event_movements = by_event[anchor.stock_entry]
			targets = [row for row in event_movements if row.direction == target_direction]
			graph[(anchor.batch_no, anchor.item_code)].append(
				make_event_payload(anchor, event_movements, targets)
			)
			for target in targets:
				if not safe_link("Batch", target.batch_no) or not safe_link("Item", target.item_code):
					continue
				key = (target.batch_no, target.item_code)
				if len(discovered) >= MAX_BATCH_NODES:
					truncated = True
					break
				discovered.add(key)
				next_frontier.add(key)
		frontier = next_frontier
	else:
		truncated = bool(frontier)
	return graph, discovered, truncated


def apply_event_filters(query_filters, filters):
	field_map = {
		"manufacturing_stage": "manufacturing_stage",
		"work_order": "work_order",
		"production_job": "production_job",
		"company": "company",
	}
	for source, target in field_map.items():
		if filters.get(source):
			query_filters[target] = filters[source]
	if filters.get("from_date") and filters.get("to_date"):
		query_filters["posting_datetime"] = ["between", [filters["from_date"], filters["to_date"]]]
	elif filters.get("from_date"):
		query_filters["posting_datetime"] = [">=", filters["from_date"]]
	elif filters.get("to_date"):
		query_filters["posting_datetime"] = ["<=", filters["to_date"]]


def can_read_stock_entry(stock_entry):
	try:
		return bool(frappe.has_permission("Stock Entry", ptype="read", doc=stock_entry))
	except frappe.PermissionError:
		return False


def make_event_payload(anchor, movements, targets):
	def serialize(row):
		if not safe_link("Batch", row.batch_no) or not safe_link("Item", row.item_code):
			return None
		return {
			"batch_no": row.batch_no,
			"item_code": row.item_code,
			"quantity": row.quantity,
			"uom": row.uom,
			"warehouse": safe_link("Warehouse", row.warehouse),
			"quality_inspection": safe_link("Quality Inspection", row.quality_inspection),
		}

	inputs = [serialize(row) for row in movements if row.direction == "Input"]
	outputs = [serialize(row) for row in movements if row.direction == "Output"]
	scrap = [serialize(row) for row in movements if row.direction == "Scrap / Recovery"]
	operation_context = []
	for row in _safe_json_list(anchor.operation_context_json):
		job = safe_link("SmartEdge Production Job", row.get("production_job"))
		if not job:
			continue
		safe_row = {
			**row,
			"production_job": job,
			"operation": safe_link("Operation", row.get("operation")),
			"workstation": safe_link("Workstation", row.get("workstation")),
			"employee": safe_link("Employee", row.get("employee")),
			"related_stock_entry": safe_link("Stock Entry", row.get("related_stock_entry")),
		}
		for fieldname in ("input_batches", "output_batches"):
			safe_row[fieldname] = [
				batch
				for batch in (_sanitize_operation_batch(value) for value in row.get(fieldname) or [])
				if batch
			]
		operation_context.append(safe_row)
	quality_inspections = _unique(
		[
			safe_link("Quality Inspection", row.quality_inspection)
			for row in movements
			if row.get("quality_inspection")
		]
	)
	return {
		"stock_entry": anchor.stock_entry,
		"stage": anchor.manufacturing_stage,
		"posting_datetime": anchor.posting_datetime,
		"company": anchor.company,
		"work_order": safe_link("Work Order", anchor.work_order),
		"production_job": safe_link("SmartEdge Production Job", anchor.production_job),
		"production_jobs": [
			name for name in _safe_json_list(anchor.production_jobs_json)
			if safe_link("SmartEdge Production Job", name)
		],
		"plan_entry": safe_link("SmartEdge Production Plan Entry", anchor.plan_entry),
		"final_eb_bom_generator": safe_link("Final EB BOM Generator", anchor.final_eb_bom_generator),
		"operation": safe_link("Operation", anchor.operation),
		"operations": [
			name for name in _safe_json_list(anchor.operations_json) if safe_link("Operation", name)
		],
		"operation_context": operation_context,
		"quality_inspections": quality_inspections,
		"workstation": safe_link("Workstation", anchor.workstation),
		"employee": safe_link("Employee", anchor.employee),
		"actual_start": anchor.actual_start,
		"actual_end": anchor.actual_end,
		"good_quantity": anchor.good_quantity,
		"rejected_quantity": anchor.rejected_quantity,
		"rework_quantity": anchor.rework_quantity,
		"usable_scrap": anchor.usable_scrap,
		"unusable_scrap": anchor.unusable_scrap,
		"anchor_movement": serialize(anchor),
		"inputs": [row for row in inputs if row],
		"outputs": [row for row in outputs if row],
		"scrap_recovery": [row for row in scrap if row],
		"target_keys": [
			(row.batch_no, row.item_code) for row in targets
			if safe_link("Batch", row.batch_no) and safe_link("Item", row.item_code)
		],
		"allocation_basis": "Manufacturing Event",
	}


def _sanitize_operation_batch(row):
	if not isinstance(row, dict):
		return None
	batch_no = safe_link("Batch", row.get("batch_no"))
	item_code = safe_link("Item", row.get("item_code"))
	if not batch_no or not item_code:
		return None
	return {
		**row,
		"batch_no": batch_no,
		"item_code": item_code,
		"warehouse": safe_link("Warehouse", row.get("warehouse")),
		"quality_inspection": safe_link("Quality Inspection", row.get("quality_inspection")),
	}


def safe_link(doctype, name):
	if not name:
		return None
	return name if _can_read_link(frappe.session.user, doctype, name) else None


@lru_cache(maxsize=4096)
def _can_read_link(user, doctype, name):
	try:
		return bool(frappe.has_permission(doctype, ptype="read", doc=name, user=user))
	except (frappe.PermissionError, frappe.DoesNotExistError):
		return False


def get_batch_details(keys):
	batch_names = list({key[0] for key in keys})
	batch_fields = ["name", "item", "manufacturing_date", "expiry_date", "batch_qty"]
	if frappe.get_meta("Batch", cached=True).has_field("disabled"):
		batch_fields.append("disabled")
	rows = frappe.get_list(
		"Batch",
		filters={"name": ["in", batch_names]},
		fields=batch_fields,
		limit_page_length=MAX_BATCH_NODES,
	)
	movements = frappe.get_list(
		MOVEMENT_DOCTYPE,
		filters={"batch_no": ["in", batch_names], "source_status": ACTIVE},
		fields=[
			"batch_no",
			"item_code",
			"direction",
			"quantity",
			"uom",
			"warehouse",
			"manufacturing_stage",
			"posting_datetime",
		],
		order_by="posting_datetime desc",
		limit_page_length=MAX_MOVEMENTS_PER_LEVEL,
	)
	evidence = {}
	for movement in movements:
		key = (movement.batch_no, movement.item_code)
		current = evidence.get(key)
		if not current or (current.direction != "Output" and movement.direction == "Output"):
			evidence[key] = movement
	for row in rows:
		movement = evidence.get((row.name, row.item))
		if movement:
			row.manufacturing_stage = movement.manufacturing_stage
			row.production_quantity = movement.quantity
			row.production_uom = movement.uom
			row.warehouse = movement.warehouse
			row.production_datetime = movement.posting_datetime
	return {(row.name, row.item): row for row in rows}


def build_genealogy_tree(key, graph, details, path, counter):
	counter[0] += 1
	batch = details.get(key) or frappe._dict({"name": key[0], "item": key[1]})
	node = {
		"batch_no": key[0],
		"item_code": key[1],
		"manufacturing_date": batch.get("manufacturing_date"),
		"expiry_date": batch.get("expiry_date"),
		"batch_quantity": batch.get("batch_qty"),
		"batch_status": "Disabled" if cint(batch.get("disabled")) else "Active",
		"manufacturing_stage": batch.get("manufacturing_stage"),
		"production_quantity": batch.get("production_quantity"),
		"production_uom": batch.get("production_uom"),
		"warehouse": batch.get("warehouse"),
		"production_datetime": batch.get("production_datetime"),
		"events": [],
	}
	if key in path:
		node["cycle"] = True
		return node
	if counter[0] > MAX_BATCH_NODES:
		node["truncated"] = True
		return node
	new_path = set(path)
	new_path.add(key)
	for event in graph.get(key, []):
		payload = {field: value for field, value in event.items() if field != "target_keys"}
		payload["children"] = [
			build_genealogy_tree(target, graph, details, new_path, counter)
			for target in event["target_keys"]
		]
		node["events"].append(payload)
	return node


def _safe_json_list(value):
	try:
		parsed = json.loads(value or "[]")
		return parsed if isinstance(parsed, list) else []
	except (TypeError, ValueError):
		return []


@frappe.whitelist()
def rebuild_genealogy(from_date=None, to_date=None, stock_entry=None, batch_no=None, limit=500):
	allowed_roles = {"System Manager", "Manufacturing Manager"}
	if not allowed_roles.intersection(frappe.get_roles()):
		frappe.throw(_("Only a System Manager or Manufacturing Manager can rebuild genealogy."), frappe.PermissionError)
	limit = min(max(cint(limit), 1), 500)
	filters = {"docstatus": 1, "purpose": "Manufacture"}
	if stock_entry:
		filters["name"] = stock_entry
	if from_date and to_date:
		filters["posting_date"] = ["between", [from_date, to_date]]
	elif from_date:
		filters["posting_date"] = [">=", from_date]
	elif to_date:
		filters["posting_date"] = ["<=", to_date]
	if batch_no:
		stock_entries = find_stock_entries_for_batch(batch_no)
		filters["name"] = ["in", stock_entries or [""]]
	rows = frappe.get_list(
		"Stock Entry",
		filters=filters,
		fields=["name"],
		order_by="posting_date asc, posting_time asc",
		limit_page_length=limit,
	)
	result = {"processed": 0, "created": 0, "updated": 0, "skipped": 0, "errors": []}
	for row in rows:
		try:
			counts = capture_stock_entry(row.name)
			result["processed"] += 1
			for key in ("created", "updated", "skipped"):
				result[key] += counts[key]
		except Exception:
			frappe.log_error(title=f"Batch genealogy rebuild failed for {row.name}")
			result["errors"].append(row.name)
	return result


def find_stock_entries_for_batch(batch_no):
	detail_names = frappe.get_all("Stock Entry Detail", filters={"batch_no": batch_no}, pluck="parent")
	bundle_entries = frappe.get_all("Serial and Batch Entry", filters={"batch_no": batch_no}, pluck="parent")
	if bundle_entries:
		detail_names.extend(
			frappe.get_all(
				"Stock Entry Detail",
				filters={"serial_and_batch_bundle": ["in", bundle_entries]},
				pluck="parent",
			)
		)
	return _unique(detail_names)
