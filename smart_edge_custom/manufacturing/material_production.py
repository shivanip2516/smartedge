from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

from smart_edge_custom.manufacturing.batch_manufacturing import (
	get_complete_batch_count,
	get_item_batch_details,
	make_batch_work_order,
	validate_number_of_batches,
)
from smart_edge_custom.manufacturing.fifo_batch_selection import get_fifo_batch_availability
from smart_edge_custom.manufacturing.final_bom_generator import (
	get_base_colour_stock_item,
	get_compound_stock_item,
	get_prepared_component_batch_weight,
)

MATERIAL_PLAN_TYPE = "Required Material"
ACTIVE_PLAN_STATUSES = {"Pending", "Ready", "Planned", "In Progress", "On Hold"}
MATERIAL_CONFIG = {
	"Compound": {
		"master_doctype": "Compound",
		"master_field": "compound",
		"batch_field": "compound_consumption_batches",
		"item_resolver": get_compound_stock_item,
		"status_field": "compound_production_status",
		"plan_field": "compound_production_plan",
		"work_order_field": "compound_work_order",
		"planned_field": "compound_planned_batches",
		"completed_field": "compound_completed_batches",
	},
	"Base Colour": {
		"master_doctype": "Base Colour",
		"master_field": "base_colour",
		"batch_field": "shade_consumption_batches",
		"item_resolver": get_base_colour_stock_item,
		"status_field": "base_colour_production_status",
		"plan_field": "base_colour_production_plan",
		"work_order_field": "base_colour_work_order",
		"planned_field": "base_colour_planned_batches",
		"completed_field": "base_colour_completed_batches",
	},
}


@frappe.whitelist()
def get_material_planning_context(production):
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("read")
	return build_material_planning_context(doc)


def build_material_planning_context(doc, include_planning_details=True):
	requirements = [
		build_material_requirement(doc, material_type, include_planning_details)
		for material_type in MATERIAL_CONFIG
	]
	return {
		"production": doc.name,
		"source_warehouse": doc.source_warehouse,
		"requirements": requirements,
		"has_shortage": any(row["batches_to_plan"] > 0 for row in requirements),
		"overall_status": get_overall_material_status(requirements),
	}


def build_material_requirement(doc, material_type, include_planning_details=True):
	config = MATERIAL_CONFIG[material_type]
	master_name = doc.get(config["master_field"])
	required_batches = cint(doc.get(config["batch_field"]))
	item_code = config["item_resolver"](master_name) if master_name else None
	batch_weight_kg = (
		get_prepared_component_batch_weight(config["master_doctype"], master_name, material_type)
		if master_name and required_batches
		else 0
	)
	item_details = get_item_batch_details(item_code)
	stock_uom = item_details.get("stock_uom")
	weight_per_batch = flt(item_details.get("output_qty"))
	availability = {"available_batches": 0}
	if required_batches and item_code and doc.source_warehouse and batch_weight_kg:
		availability = get_fifo_batch_availability(
			item_code, doc.source_warehouse, batch_weight_kg, material_type
		)
	available_batches = min(cint(availability.get("available_batches")), required_batches)
	plans = get_linked_material_plans(doc.name, material_type, doc.source_warehouse)
	outstanding_batches = sum(row["outstanding_batches"] for row in plans)
	completed_batches = sum(row["completed_batches"] for row in plans)
	batches_to_plan = max(required_batches - available_batches - outstanding_batches, 0)
	latest = plans[0] if plans else {}
	bom = get_active_batch_bom(item_code) if include_planning_details else None
	operations = get_material_operations(bom) if include_planning_details else []
	operation = operations[0] if operations else {}
	workstation_context = (
		get_material_workstation_context(bom, operation.get("operation"))
		if operation.get("operation")
		else {"workstations": [], "restricted": 0, "default_workstation": None}
	)
	warehouses = get_default_warehouses(doc, bom) if include_planning_details else {}
	status = get_material_status(
		required_batches, available_batches, outstanding_batches, completed_batches, plans
	)
	return {
		"material_type": material_type,
		"master": master_name,
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name") if item_code else None,
		"required_batches": required_batches,
		"available_batches": available_batches,
		"already_planned_batches": outstanding_batches,
		"batches_to_plan": batches_to_plan,
		"completed_batches": completed_batches,
		"weight_per_batch": weight_per_batch,
		"total_manufacturing_quantity": flt(Decimal(str(batches_to_plan)) * Decimal(str(weight_per_batch))),
		"stock_uom": stock_uom,
		"bom_no": bom,
		"operation": operation.get("operation"),
		"operation_options": [row.operation for row in operations],
		"default_workstation": workstation_context.get("default_workstation"),
		"compatible_workstations": workstation_context.get("workstations"),
		"workstation_filter_applied": workstation_context.get("restricted"),
		"duration_hours": operation.get("duration_hours") or 1,
		"source_warehouse": warehouses.get("source_warehouse"),
		"wip_warehouse": warehouses.get("wip_warehouse"),
		"target_warehouse": warehouses.get("target_warehouse"),
		"status": status,
		"production_plan": latest.get("name"),
		"work_order": latest.get("work_order"),
	}


def get_linked_material_plans(production, material_type, required_target_warehouse):
	rows = frappe.get_all(
		"SmartEdge Production Plan Entry",
		filters={
			"final_eb_bom_generator": production,
			"plan_type": MATERIAL_PLAN_TYPE,
			"material_type": material_type,
			"status": ["!=", "Cancelled"],
		},
		fields=["name", "status", "work_order", "planned_batches", "weight_per_batch", "target_warehouse"],
		order_by="creation desc",
		limit_page_length=100,
	)
	for row in rows:
		row.completed_batches = get_work_order_completed_batches(row.work_order, row.weight_per_batch)
		row.outstanding_batches = (
			max(cint(row.planned_batches) - row.completed_batches, 0)
			if row.status in ACTIVE_PLAN_STATUSES
			and row.target_warehouse == required_target_warehouse
			and is_active_work_order(row.work_order)
			else 0
		)
	return rows


def get_work_order_completed_batches(work_order, weight_per_batch):
	if not work_order or flt(weight_per_batch) <= 0:
		return 0
	produced_qty = frappe.db.get_value("Work Order", work_order, "produced_qty") or 0
	return int(Decimal(str(flt(produced_qty))) / Decimal(str(flt(weight_per_batch))))


def is_active_work_order(work_order):
	if not work_order:
		return False
	row = frappe.db.get_value("Work Order", work_order, ["docstatus", "status"], as_dict=True)
	return bool(row and row.docstatus == 1 and row.status not in {"Stopped", "Cancelled", "Completed"})


def get_active_batch_bom(item_code):
	if not item_code:
		return None
	return frappe.db.get_value(
		"BOM",
		{"item": item_code, "docstatus": 1, "is_active": 1, "custom_batch": 1},
		"name",
		order_by="is_default desc, modified desc",
	)


def get_bom_operations(bom_no):
	if not bom_no:
		return []
	rows = frappe.get_all(
		"BOM Operation",
		filters={"parent": bom_no, "parenttype": "BOM"},
		fields=["operation", "workstation", "time_in_mins"],
		order_by="idx asc",
		limit_page_length=100,
	)
	result = []
	seen = set()
	for row in rows:
		if not row.operation or row.operation in seen:
			continue
		seen.add(row.operation)
		row.duration_hours = flt(row.time_in_mins) / 60 if flt(row.time_in_mins) else 1
		result.append(row)
	return result


def get_bom_operation(bom_no, operation=None):
	operations = get_bom_operations(bom_no)
	if operation:
		return next((row for row in operations if row.operation == operation), {})
	return operations[0] if operations else {}


def get_material_operations(bom_no):
	operations = get_bom_operations(bom_no)
	if operations:
		return operations
	# Some existing one-batch BOMs predate operation tracking. In that case use
	# the standard Operation master as the selectable configuration source.
	rows = frappe.get_all(
		"Operation", fields=["name as operation", "workstation"], order_by="name", limit_page_length=500
	)
	for row in rows:
		row.time_in_mins = 0
		row.duration_hours = 1
	return rows


def get_material_workstation_context(bom_no, operation):
	frappe.get_doc("BOM", bom_no).check_permission("read")
	operation_doc = frappe.get_doc("Operation", operation)
	operation_doc.check_permission("read")
	bom_operations = get_bom_operations(bom_no)
	operation_row = next((row for row in bom_operations if row.operation == operation), None)
	if bom_operations and not operation_row:
		frappe.throw(_("Operation {0} is not configured on BOM {1}.").format(operation, bom_no))
	from smart_edge_custom.manufacturing.planning import get_compatible_workstations

	# Custom compatibility tables/routes are restrictions. Standard Operation
	# and BOM workstation values are defaults, not one-item allow-lists.
	# EB process-route assignments are route-specific and must not constrain a
	# standalone Compound/Base Colour material operation.
	workstations = get_compatible_workstations(operation, include_default=0, include_routes=0)
	workstations = filter_enabled_workstations(workstations)
	restricted = bool(workstations)
	if not restricted:
		frappe.has_permission("Workstation", "read", throw=True)
		workstations = frappe.get_all(
			"Workstation", filters={"disabled": 0}, pluck="name", order_by="name", limit_page_length=500
		)
	default_workstation = (
		operation_row.get("workstation") if operation_row else operation_doc.get("workstation")
	)
	if default_workstation not in workstations:
		default_workstation = workstations[0] if restricted and workstations else None
	return {
		"operation": operation,
		"workstations": workstations,
		"restricted": cint(restricted),
		"default_workstation": default_workstation,
		"duration_hours": operation_row.duration_hours if operation_row else 1,
	}


def filter_enabled_workstations(workstations):
	if not workstations:
		return []
	enabled = set(
		frappe.get_all(
			"Workstation",
			filters={"name": ["in", workstations], "disabled": 0},
			pluck="name",
			limit_page_length=len(workstations),
		)
	)
	return [workstation for workstation in workstations if workstation in enabled]


@frappe.whitelist()
def get_material_operation_context(bom_no, operation):
	return get_material_workstation_context(bom_no, operation)


def get_default_warehouses(doc, bom_no):
	company = frappe.db.get_value(
		"Company", doc.company, ["default_wip_warehouse", "default_fg_warehouse"], as_dict=True
	) or {}
	source_warehouses = frappe.get_all(
		"BOM Item", filters={"parent": bom_no}, pluck="source_warehouse", limit_page_length=100
	) if bom_no else []
	source_warehouses = [warehouse for warehouse in source_warehouses if warehouse]
	return {
		"source_warehouse": source_warehouses[0] if len(set(source_warehouses)) == 1 else None,
		"wip_warehouse": company.get("default_wip_warehouse"),
		"target_warehouse": doc.source_warehouse or company.get("default_fg_warehouse"),
	}


@frappe.whitelist()
def get_material_raw_stock(production, material_type, source_warehouse):
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("read")
	if material_type not in MATERIAL_CONFIG:
		frappe.throw(_("Select a valid Material Type."))
	if source_warehouse:
		validate_warehouse(source_warehouse, doc.company, _("Source Warehouse"))
	requirement = build_material_requirement(doc, material_type)
	return get_raw_material_availability(doc, requirement, source_warehouse)


def get_raw_material_availability(doc, requirement, source_warehouse):
	if not source_warehouse:
		return {"available": 0, "materials": [], "message": _("Source Warehouse is required.")}
	if not requirement.get("bom_no") or requirement.get("batches_to_plan", 0) <= 0:
		return {"available": 1, "materials": []}

	work_order = make_batch_work_order(
		requirement["bom_no"],
		requirement["item_code"],
		requirement["batches_to_plan"],
		doc.company,
	)
	work_order.source_warehouse = source_warehouse
	work_order.set_required_items(reset_source_warehouse=True)
	materials = []
	for row in work_order.required_items:
		if not cint(row.get("include_item_in_manufacturing")):
			continue
		bin_row = frappe.db.get_value(
			"Bin",
			{"item_code": row.item_code, "warehouse": source_warehouse},
			["actual_qty", "reserved_qty_for_production"],
			as_dict=True,
		) or {}
		actual_qty = flt(bin_row.get("actual_qty"))
		reserved_qty = flt(bin_row.get("reserved_qty_for_production"))
		available_qty = max(actual_qty - reserved_qty, 0)
		required_qty = flt(row.required_qty)
		materials.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name or row.item_code,
				"warehouse": source_warehouse,
				"uom": row.stock_uom,
				"required_qty": required_qty,
				"actual_qty": actual_qty,
				"reserved_qty": reserved_qty,
				"available_qty": available_qty,
				"shortage_qty": max(required_qty - available_qty, 0),
			}
		)
	return {
		"available": cint(all(row["shortage_qty"] <= 0.000001 for row in materials)),
		"materials": materials,
	}


def validate_raw_material_availability(doc, requirement, source_warehouse):
	stock = get_raw_material_availability(doc, requirement, source_warehouse)
	shortages = [row for row in stock.get("materials") or [] if row["shortage_qty"] > 0.000001]
	if not shortages:
		return
	details = "<br>".join(
		_("{0}: required {1} {2}, available {3} {2} in {4}").format(
			frappe.bold(row["item_code"]),
			flt(row["required_qty"]),
			row["uom"],
			flt(row["available_qty"]),
			frappe.bold(source_warehouse),
		)
		for row in shortages
	)
	frappe.throw(_("Insufficient BOM raw materials for {0}:<br>{1}").format(requirement["material_type"], details))


def get_material_status(required, available, outstanding, completed, plans):
	if required <= 0:
		return "Stock Available"
	if available >= required:
		return "Stock Available"
	job_statuses = get_material_job_statuses([row.name for row in plans])
	if "In Progress" in job_statuses:
		return "In Progress"
	if "Paused" in job_statuses:
		return "Paused"
	if completed >= required:
		return "Partially Completed"
	if completed or any(row.status == "Completed" for row in plans):
		return "Partially Completed"
	if outstanding:
		return "Planned"
	return "Shortage"


def get_material_job_statuses(plan_entries):
	if not plan_entries:
		return set()
	return set(
		frappe.get_all(
			"SmartEdge Production Job",
			filters={"plan_entry": ["in", plan_entries], "status": ["!=", "Cancelled"]},
			pluck="status",
			limit_page_length=100,
		)
	)


def get_overall_material_status(requirements):
	required = [row for row in requirements if row["required_batches"] > 0]
	if not required or all(row["status"] == "Stock Available" for row in required):
		return "Ready to Plan"
	return "Waiting for Raw Material"


@frappe.whitelist()
def create_required_material_plans(production, plans):
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("write")
	if not frappe.has_permission("SmartEdge Production Plan Entry", "create"):
		frappe.throw(_("Not permitted to create production plans."), frappe.PermissionError)
	plans = frappe.parse_json(plans) if isinstance(plans, str) else plans or []
	requested = {row.get("material_type"): row for row in plans}
	lock_material_requirement(doc.name)
	doc.reload()
	reconcile_material_plans(doc)
	context = build_material_planning_context(doc)
	created = []
	for requirement in context["requirements"]:
		if requirement["material_type"] not in requested or requirement["batches_to_plan"] <= 0:
			continue
		values = requested[requirement["material_type"]]
		created.append(create_material_plan(doc, requirement, values))
	refresh_material_readiness(doc.name)
	return {"created": created, "context": build_material_planning_context(doc)}


@frappe.whitelist()
def reconcile_required_material_plans(production):
	doc = frappe.get_doc("Final EB BOM Generator", production)
	doc.check_permission("write")
	lock_material_requirement(doc.name)
	doc.reload()
	created = reconcile_material_plans(doc)
	context = refresh_material_readiness(doc.name)
	return {"created": created, "context": context}


def reconcile_material_plans(doc):
	"""Restore only explicitly linked material Work Orders that are missing a plan entry."""
	if not frappe.get_meta("Work Order", cached=True).has_field("custom_final_eb_bom_generator"):
		return []
	context = build_material_planning_context(doc)
	created = []
	for requirement in context["requirements"]:
		material_type = requirement["material_type"]
		if frappe.db.exists(
			"SmartEdge Production Plan Entry",
			{
				"final_eb_bom_generator": doc.name,
				"plan_type": MATERIAL_PLAN_TYPE,
				"material_type": material_type,
				"status": ["!=", "Cancelled"],
			},
		):
			continue
		config = MATERIAL_CONFIG[material_type]
		candidates = []
		linked_work_order = doc.get(config["work_order_field"])
		if linked_work_order:
			candidates.append(linked_work_order)
		for name in frappe.get_all(
			"Work Order",
			filters={
				"custom_final_eb_bom_generator": doc.name,
				"custom_material_type": material_type,
				"docstatus": 1,
				"status": ["not in", ["Completed", "Stopped", "Cancelled"]],
			},
			pluck="name",
			order_by="creation desc",
			limit_page_length=20,
		):
			if name not in candidates:
				candidates.append(name)
		for name in candidates:
			entry = reconcile_work_order_plan(doc, requirement, name)
			if entry:
				created.append(
					{"plan_entry": entry.name, "work_order": name, "material_type": material_type}
				)
				break
	return created


def reconcile_work_order_plan(doc, requirement, work_order_name):
	if not work_order_name or not frappe.db.exists("Work Order", work_order_name):
		return None
	if frappe.db.exists(
		"SmartEdge Production Plan Entry",
		{"work_order": work_order_name, "plan_type": MATERIAL_PLAN_TYPE, "status": ["!=", "Cancelled"]},
	):
		return None
	work_order = frappe.get_doc("Work Order", work_order_name)
	if (
		work_order.docstatus != 1
		or work_order.status in {"Completed", "Stopped", "Cancelled"}
		or work_order.company != doc.company
		or work_order.production_item != requirement["item_code"]
		or work_order.bom_no != requirement["bom_no"]
	):
		return None
	if work_order.get("custom_final_eb_bom_generator") not in (None, "", doc.name):
		return None
	if work_order.get("custom_material_type") not in (None, "", requirement["material_type"]):
		return None
	operation = work_order.get("custom_material_operation")
	workstation = work_order.get("custom_material_workstation")
	if not operation or not workstation:
		return None
	operation_context = get_material_workstation_context(requirement["bom_no"], operation)
	validate_material_workstation(
		workstation, operation_context.get("workstations"), bool(operation_context.get("restricted"))
	)
	values = {
		"operation": operation,
		"workstation": workstation,
		"planned_start": now_datetime(),
		"source_warehouse": work_order.source_warehouse,
		"wip_warehouse": work_order.wip_warehouse,
		"target_warehouse": work_order.fg_warehouse,
	}
	return create_material_plan_entry(doc, requirement, values, work_order, operation_context)


def create_material_plan(doc, requirement, values):
	batches = validate_number_of_batches(requirement["batches_to_plan"], _("Batches to Plan"))
	if not requirement.get("bom_no"):
		frappe.throw(_("No active submitted one-batch BOM found for Item {0}.").format(requirement["item_code"]))
	operation = values.get("operation")
	if not operation:
		frappe.throw(_("BOM {0} has no configured manufacturing Operation.").format(requirement["bom_no"]))
	operation_context = get_material_workstation_context(requirement["bom_no"], operation)
	workstation = values.get("workstation")
	validate_material_workstation(
		workstation,
		operation_context.get("workstations"),
		bool(operation_context.get("restricted")),
	)
	from smart_edge_custom.manufacturing.planning import validate_employee

	validate_employee(values.get("employee"), doc.company)
	warehouses = {
		"source_warehouse": values.get("source_warehouse") or requirement.get("source_warehouse"),
		"wip_warehouse": values.get("wip_warehouse") or requirement.get("wip_warehouse"),
		"target_warehouse": values.get("target_warehouse") or requirement.get("target_warehouse"),
	}
	for fieldname, label in (
		("source_warehouse", _("Source Warehouse")),
		("wip_warehouse", _("Work-in-Progress Warehouse")),
		("target_warehouse", _("Target Warehouse")),
	):
		validate_warehouse(warehouses[fieldname], doc.company, label)
	if not values.get("planned_start"):
		frappe.throw(_("Planned Start is required."))
	validate_raw_material_availability(doc, requirement, warehouses["source_warehouse"])

	work_order = make_batch_work_order(
		requirement["bom_no"], requirement["item_code"], batches, doc.company
	)
	work_order.source_warehouse = warehouses["source_warehouse"]
	work_order.wip_warehouse = warehouses["wip_warehouse"]
	work_order.fg_warehouse = warehouses["target_warehouse"]
	set_work_order_material_origin(work_order, doc.name, requirement["material_type"], operation, workstation)
	work_order.set_required_items(reset_source_warehouse=True)
	work_order.insert()
	work_order.submit()
	entry = create_material_plan_entry(doc, requirement, values, work_order, operation_context)
	return {"plan_entry": entry.name, "work_order": work_order.name, "material_type": requirement["material_type"]}


def create_material_plan_entry(doc, requirement, values, work_order, operation_context=None):
	operation = values.get("operation")
	workstation = values.get("workstation")
	operation_context = operation_context or get_material_workstation_context(requirement["bom_no"], operation)
	batches = cint(work_order.get("custom_no_of_batches")) or cint(requirement.get("batches_to_plan"))
	warehouses = {
		"source_warehouse": values.get("source_warehouse") or work_order.source_warehouse,
		"wip_warehouse": values.get("wip_warehouse") or work_order.wip_warehouse,
		"target_warehouse": values.get("target_warehouse") or work_order.fg_warehouse,
	}
	planned_start = get_datetime(values.get("planned_start") or now_datetime())
	duration_hours = flt(operation_context.get("duration_hours")) or 1
	entry = frappe.new_doc("SmartEdge Production Plan Entry")
	entry.final_eb_bom_generator = doc.name
	entry.plan_type = MATERIAL_PLAN_TYPE
	entry.material_type = requirement["material_type"]
	entry.material_item = requirement["item_code"]
	entry.bom_no = requirement["bom_no"]
	entry.work_order = work_order.name
	entry.operation = operation
	entry.workstation = workstation
	entry.planning_category = "Mixing"
	entry.sequence = 0
	entry.production_quantity = work_order.qty
	entry.planned_meter = batches
	entry.planned_kg = work_order.qty
	entry.planned_batches = batches
	entry.weight_per_batch = requirement["weight_per_batch"]
	entry.quantity_uom = requirement["stock_uom"]
	entry.source_warehouse = warehouses["source_warehouse"]
	entry.wip_warehouse = warehouses["wip_warehouse"]
	entry.target_warehouse = warehouses["target_warehouse"]
	entry.planned_employee = values.get("employee")
	entry.planned_shift = values.get("shift")
	# Keep a requested start as the scheduling-dialog default, but leave the
	# operation in Pending for Planning until a board user schedules it.
	entry.planned_date = None
	entry.planned_start_datetime = planned_start
	entry.planned_end_datetime = frappe.utils.add_to_date(planned_start, hours=duration_hours)
	entry.status = "Pending"
	entry.priority = values.get("priority") or "Normal"
	entry.remarks = _("Required {0} production for {1}").format(requirement["material_type"], doc.name)
	entry.insert()
	if work_order.meta.has_field("custom_material_plan_entry"):
		frappe.db.set_value(
			"Work Order", work_order.name, "custom_material_plan_entry", entry.name, update_modified=False
		)
	return entry


def set_work_order_material_origin(work_order, production, material_type, operation, workstation):
	values = {
		"custom_final_eb_bom_generator": production,
		"custom_material_type": material_type,
		"custom_material_operation": operation,
		"custom_material_workstation": workstation,
	}
	for fieldname, value in values.items():
		if work_order.meta.has_field(fieldname):
			work_order.set(fieldname, value)


def validate_warehouse(warehouse, company, label):
	if not warehouse:
		frappe.throw(_("{0} is required.").format(label))
	if not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("{0} must be an enabled leaf Warehouse for Company {1}.").format(label, company))
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	warehouse_doc.check_permission("read")
	if warehouse_doc.company != company or warehouse_doc.is_group or warehouse_doc.disabled:
		frappe.throw(_("{0} must be an enabled leaf Warehouse for Company {1}.").format(label, company))


def validate_material_workstation(workstation, compatible_workstations, restricted=True):
	if not workstation:
		frappe.throw(_("Workstation is required."))
	row = frappe.db.get_value("Workstation", workstation, ["disabled"], as_dict=True)
	if not row or row.disabled:
		frappe.throw(_("Select an enabled Workstation."))
	frappe.get_doc("Workstation", workstation).check_permission("read")
	if restricted and workstation not in (compatible_workstations or []):
		frappe.throw(_("Selected Workstation is not configured for this BOM Operation."))


def lock_material_requirement(production):
	frappe.db.sql("select name from `tabFinal EB BOM Generator` where name=%s for update", (production,))


def is_material_plan(entry):
	return entry.get("plan_type") == MATERIAL_PLAN_TYPE


def ensure_material_transfer(entry):
	return ensure_work_order_stock_entry(entry, "Material Transfer for Manufacture", flt(entry.production_quantity))


def complete_material_production(entry, completed_batches):
	completed_batches = validate_number_of_batches(completed_batches, _("Completed Batches"))
	weight = flt(entry.weight_per_batch)
	work_order = frappe.get_doc("Work Order", entry.work_order)
	remaining_qty = max(flt(work_order.qty) - flt(work_order.produced_qty), 0)
	if remaining_qty <= 0:
		frappe.throw(_("Work Order {0} has no remaining quantity to manufacture.").format(work_order.name))
	remaining_batches = get_complete_batch_count(remaining_qty, weight, _("Remaining Work Order quantity"))
	if completed_batches > remaining_batches:
		frappe.throw(_("Completed Batches cannot exceed the {0} remaining batches.").format(remaining_batches))
	quantity = flt(Decimal(str(completed_batches)) * Decimal(str(weight)))
	stock_entry = ensure_work_order_stock_entry(entry, "Manufacture", quantity)
	return {"stock_entry": stock_entry, "quantity": quantity, "completed_batches": completed_batches}


def ensure_work_order_stock_entry(entry, purpose, quantity):
	existing = frappe.get_all(
		"Stock Entry",
		filters={"work_order": entry.work_order, "purpose": purpose, "docstatus": ["<", 2]},
		fields=["name", "docstatus", "fg_completed_qty"],
		order_by="creation asc",
		limit_page_length=100,
	)
	if purpose == "Material Transfer for Manufacture" and existing:
		stock_entry = frappe.get_doc("Stock Entry", existing[0].name)
		if stock_entry.docstatus == 0:
			stock_entry.submit()
		if flt(stock_entry.fg_completed_qty) + 0.000001 < flt(quantity):
			frappe.throw(
				_("Material Transfer {0} does not cover the full planned manufacturing quantity.").format(
					stock_entry.name
				)
			)
		entry.db_set("material_transfer_stock_entry", stock_entry.name, update_modified=False)
		return stock_entry.name

	if purpose == "Manufacture":
		posted = sum(flt(row.fg_completed_qty) for row in existing if row.docstatus == 1)
		work_order = frappe.get_doc("Work Order", entry.work_order)
		if posted >= flt(work_order.qty):
			return existing[-1].name
		drafts = [row for row in existing if row.docstatus == 0]
		if drafts:
			stock_entry = frappe.get_doc("Stock Entry", drafts[0].name)
			if abs(flt(stock_entry.fg_completed_qty) - flt(quantity)) > 0.000001:
				frappe.throw(
					_("Draft Manufacture Stock Entry {0} has a different quantity. Review it before retrying.").format(
						stock_entry.name
					)
				)
			stock_entry.submit()
			entry.db_set("manufacture_stock_entry", stock_entry.name, update_modified=False)
			return stock_entry.name

	from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry

	stock_entry = frappe.get_doc(make_stock_entry(entry.work_order, purpose, quantity))
	stock_entry.insert()
	stock_entry.submit()
	fieldname = "material_transfer_stock_entry" if purpose == "Material Transfer for Manufacture" else "manufacture_stock_entry"
	entry.db_set(fieldname, stock_entry.name, update_modified=False)
	return stock_entry.name


def refresh_material_readiness(production):
	if not production or not frappe.db.exists("Final EB BOM Generator", production):
		return None
	doc = frappe.get_doc("Final EB BOM Generator", production)
	# Readiness refreshes run from Stock Entry and Production Job hooks. They only
	# need quantities/statuses and must not acquire Workstation read permissions.
	context = build_material_planning_context(doc, include_planning_details=False)
	updates = {"material_readiness_status": context["overall_status"]}
	for requirement in context["requirements"]:
		config = MATERIAL_CONFIG[requirement["material_type"]]
		updates.update(
			{
				config["status_field"]: requirement["status"],
				config["plan_field"]: requirement.get("production_plan"),
				config["work_order_field"]: requirement.get("work_order"),
				config["planned_field"]: requirement.get("already_planned_batches"),
				config["completed_field"]: requirement.get("completed_batches"),
			}
		)
	frappe.db.set_value("Final EB BOM Generator", production, updates, update_modified=False)
	return context


def sync_material_readiness_from_work_order(doc, method=None):
	from smart_edge_custom.manufacturing.work_order_sync import sync_work_order_to_planning

	sync_work_order_to_planning(doc, refresh_readiness=False)
	productions = set()
	if doc.get("custom_final_eb_bom_generator"):
		productions.add(doc.custom_final_eb_bom_generator)
	if doc.name and frappe.db.exists("DocType", "SmartEdge Production Plan Entry"):
		productions.update(
			frappe.get_all(
				"SmartEdge Production Plan Entry",
				filters={"work_order": doc.name, "plan_type": MATERIAL_PLAN_TYPE},
				pluck="final_eb_bom_generator",
				limit_page_length=100,
			)
		)
	refresh_linked_material_productions(productions)


def sync_material_readiness_from_stock_entry(doc, method=None):
	productions = set()
	if doc.work_order:
		from smart_edge_custom.manufacturing.work_order_sync import sync_work_order_to_planning

		sync_work_order_to_planning(doc.work_order, refresh_readiness=False)
		productions.update(
			frappe.get_all(
				"SmartEdge Production Plan Entry",
				filters={"work_order": doc.work_order, "plan_type": MATERIAL_PLAN_TYPE},
				pluck="final_eb_bom_generator",
				limit_page_length=100,
			)
		)

	item_warehouses = set()
	for row in doc.get("items") or []:
		for warehouse in (row.s_warehouse, row.t_warehouse):
			if row.item_code and warehouse:
				item_warehouses.add((row.item_code, warehouse))
	if item_warehouses:
		prepared_items = set(
			frappe.get_all(
				"Item",
				filters={
					"name": ["in", [item for item, _warehouse in item_warehouses]],
					"item_group": ["in", ["Compounds", "Base Colour"]],
				},
				pluck="name",
				limit_page_length=len(item_warehouses),
			)
		)
		for item_code, warehouse in item_warehouses:
			if item_code not in prepared_items:
				continue
			for fieldname in ("compound_item", "base_colour_item"):
				productions.update(
					frappe.get_all(
						"Final EB BOM Generator",
						filters={"source_warehouse": warehouse, fieldname: item_code},
						pluck="name",
						limit_page_length=500,
					)
				)
	refresh_linked_material_productions(productions)


def refresh_linked_material_productions(productions):
	for production in sorted(production for production in productions if production):
		if frappe.db.exists("Final EB BOM Generator", production):
			refresh_material_readiness(production)
