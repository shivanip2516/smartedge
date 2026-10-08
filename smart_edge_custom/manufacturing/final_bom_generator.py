import re

import frappe
from frappe import _
from frappe.utils import flt

MATERIAL_TOLERANCE = 0.00001
PER_MM_PER_METER = "Per mm per meter"

def get_finished_item_configuration(finished_item):
	if not finished_item:
		return {}

	frappe.has_permission("Item", "read", throw=True)

	meta = frappe.get_meta("Item", cached=True)
	fields = [
		"name",
		"item_name",
		"item_group",
		"stock_uom",
		"include_item_in_manufacturing",
		"disabled",
	]
	for fieldname in (
		"custom_size",
		"custom_base_colour",
		"custom_base_color",
		"custom_color",
		"custom_product_category",
		"custom_product_type_design_category",
		"custom_type_finish",
		"custom_type_short_code",
		"custom_customer_order_code",
		"product_code",
	):
		if meta.has_field(fieldname):
			fields.append(fieldname)

	item = frappe.db.get_value("Item", finished_item, fields, as_dict=True)
	if not item:
		frappe.throw(_("Finished Product Item {0} does not exist.").format(frappe.bold(finished_item)))

	size_value = item.get("custom_size")
	size_details = get_size_details(size_value)
	if not size_details:
		size_details = parse_size(size_value)

	printing_type = derive_printing_type(
		item.get("custom_product_type_design_category"),
		item.get("custom_type_finish"),
		item.get("custom_type_short_code"),
	)

	return {
		"finished_item_name": item.item_name,
		"item_group": item.item_group,
		"stock_uom": item.stock_uom,
		"custom_size": size_value,
		"custom_base_colour": item.get("custom_base_colour")
		or item.get("custom_base_color")
		or item.get("custom_color"),
		"custom_product_category": item.get("custom_product_category"),
		"custom_product_type_design_category": item.get("custom_product_type_design_category"),
		"custom_type_finish": item.get("custom_type_finish"),
		"custom_type_short_code": item.get("custom_type_short_code"),
		"custom_customer_order_code": item.get("custom_customer_order_code") or item.get("product_code"),
		"thickness_mm": size_details.get("thickness"),
		"width_mm": size_details.get("width"),
		"weight_factor": size_details.get("factor"),
		"printing_type": printing_type,
		"printing_route": "None" if printing_type == "Solid" else "",
		"extrusion_type": "Single Mould Extrusion",
		"slitting_requirement": "None",
		"source_warehouse": get_default_source_warehouse(finished_item),
	}


def get_size_details(size_name):
	if not size_name or not frappe.db.exists("DocType", "Size Weight"):
		return {}

	if not frappe.db.exists("Size Weight", size_name):
		return {}

	return frappe.db.get_value(
		"Size Weight",
		size_name,
		["thickness", "width", "factor"],
		as_dict=True,
	) or {}


def get_default_source_warehouse(item_code):
	if not item_code:
		return None

	return frappe.db.get_value("Item Default", {"parent": item_code}, "default_warehouse")


def parse_size(size_value):
	if not size_value:
		return {}

	match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|X|\*|×)\s*(\d+(?:\.\d+)?)", str(size_value))
	if not match:
		return {}

	return {"thickness": flt(match.group(1)), "width": flt(match.group(2))}


def derive_printing_type(product_type=None, finish=None, short_code=None):
	text = " ".join(str(value or "") for value in (product_type, finish, short_code)).lower()
	code = str(short_code or "").upper()

	if "wghg" in text or code == "WGHG":
		return "WGHG"
	if "high gloss" in text or code in {"HG", "SHG"} or "hg" in code:
		return "High Gloss"
	if "wood grain" in text or code in {"WG", "WGM"}:
		return "Wood Grain"
	return "Solid"


def calculate_weight(thickness_mm, width_mm, weight_factor, production_quantity):
	weight_per_meter_gm = flt(thickness_mm) * flt(width_mm) * flt(weight_factor)
	weight_per_meter_kg = weight_per_meter_gm / 1000
	total_eb_weight_kg = weight_per_meter_kg * flt(production_quantity)
	return {
		"weight_per_meter_gm": weight_per_meter_gm,
		"weight_per_meter_kg": weight_per_meter_kg,
		"total_eb_weight_kg": total_eb_weight_kg,
	}


def get_compound_stock_item(compound_doc):
	if isinstance(compound_doc, str):
		compound_doc = frappe.get_doc("Compound", compound_doc)

	return resolve_stock_item(compound_doc, ("item", "item_code", "compound_item"), "compound_name", "Compound")


def get_base_colour_stock_item(base_colour_doc):
	if isinstance(base_colour_doc, str):
		base_colour_doc = frappe.get_doc("Base Colour", base_colour_doc)

	return resolve_stock_item(
		base_colour_doc,
		("item", "item_code", "base_colour_item", "colour_item"),
		"colour_name",
		"Base Colour",
	)


def resolve_stock_item(doc, explicit_fields, name_field, label):
	for fieldname in explicit_fields:
		if doc.meta.has_field(fieldname) and doc.get(fieldname) and frappe.db.exists("Item", doc.get(fieldname)):
			return doc.get(fieldname)

	if doc.meta.has_field(name_field) and doc.get(name_field) and frappe.db.exists("Item", doc.get(name_field)):
		return doc.get(name_field)

	if frappe.db.exists("Item", doc.name):
		return doc.name

	frappe.throw(
		_("{0} {1} is not linked to an existing Item. Configure the stock Item before planning production.").format(
			label, frappe.bold(doc.name)
		)
	)


def get_stock_balance(item_code, warehouse=None):
	if not item_code or not warehouse:
		return 0

	from erpnext.stock.utils import get_stock_balance as erpnext_get_stock_balance

	return flt(erpnext_get_stock_balance(item_code, warehouse))


def get_valuation_rate(item_code, company=None, warehouse=None):
	if not item_code:
		return 0

	return flt(frappe.db.get_value("Item", item_code, "valuation_rate"))


def calculate_component_requirements(doc):
	production_quantity = flt(doc.get("production_quantity"))
	compound_item = get_compound_stock_item(doc.get("compound")) if doc.get("compound") else None
	base_colour_item = get_base_colour_stock_item(doc.get("base_colour")) if doc.get("base_colour") else None

	components = []
	for component_type, item_code, consumption in (
		("Compound", compound_item, doc.get("compound_consumption_per_meter")),
		("Base Colour / Shade", base_colour_item, doc.get("shade_consumption_per_meter")),
	):
		if not item_code:
			continue

		item = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True)
		total_quantity = flt(consumption) * production_quantity
		available_stock = get_stock_balance(item_code, doc.get("source_warehouse"))
		valuation_rate = get_valuation_rate(item_code, doc.get("company"), doc.get("source_warehouse"))
		components.append(
			{
				"item_code": item_code,
				"item_name": item.item_name if item else item_code,
				"component_type": component_type,
				"consumption_per_meter": flt(consumption),
				"total_quantity": total_quantity,
				"uom": item.stock_uom if item else "Kg",
				"valuation_rate": valuation_rate,
				"amount": valuation_rate * total_quantity,
				"available_stock": available_stock,
				"shortage_qty": available_stock - total_quantity,
			}
		)

	return components


def get_component_weight_per_meter_kg(materials):
	return sum(normalize_material_qty_to_kg(row.get("consumption_per_meter"), row.get("uom")) for row in materials)


def normalize_material_qty_to_kg(quantity, uom):
	uom = str(uom or "").strip().lower()
	if uom in {"g", "gm", "gram", "grams"}:
		return flt(quantity) / 1000
	return flt(quantity)


def calculate_consumable_costs(doc, route_doc):
	rows = []
	missing = []
	width = flt(doc.get("width_mm"))
	production_quantity = flt(doc.get("production_quantity"))
	total_weight = flt(doc.get("total_eb_weight_kg"))

	if not route_doc:
		return rows, missing

	for route_cost in route_doc.get("cost_heads") or []:
		if not route_cost.cost_head:
			continue

		cost_head = frappe.db.get_value(
			"SmartEdge Cost Head",
			route_cost.cost_head,
			["name", "linked_item", "basis", "rate", "enabled"],
			as_dict=True,
		)
		if not cost_head or not cost_head.enabled:
			missing.append(route_cost.cost_head)
			continue

		cost_per_meter, total_cost = calculate_cost_head_amount(
			cost_head.basis,
			cost_head.rate,
			width,
			production_quantity,
			total_weight,
		)
		rows.append(
			{
				"cost_head": cost_head.name,
				"item": cost_head.linked_item,
				"rate_basis": cost_head.basis,
				"rate": flt(cost_head.rate),
				"width": width,
				"cost_per_meter": cost_per_meter,
				"total_cost": total_cost,
			}
		)

	return rows, missing


def calculate_cost_head_amount(basis, rate, width, production_quantity, total_weight):
	rate = flt(rate)
	if basis == "Per mm per meter":
		cost_per_meter = width * rate
		return cost_per_meter, cost_per_meter * production_quantity
	if basis == "Per meter":
		return rate, rate * production_quantity
	if basis == "Per Kg":
		total_cost = rate * total_weight
		return (total_cost / production_quantity if production_quantity else 0), total_cost
	if basis == "Fixed":
		return (rate / production_quantity if production_quantity else 0), rate
	return 0, 0


def resolve_process_route(doc, throw=False):
	if not frappe.db.exists("DocType", "SmartEdge Process Route"):
		message = _("SmartEdge Process Route DocType is not installed.")
		if throw:
			frappe.throw(message)
		return None, message

	product_type = get_route_filter_value(
		doc.get("custom_product_type_design_category") or doc.get("printing_type")
	)
	filters = {
		"enabled": 1,
		"printing_type": get_route_filter_value(doc.get("printing_type"), "Solid"),
		"printing_route": get_route_filter_value(doc.get("printing_route"), "None"),
		"extrusion_type": get_route_filter_value(doc.get("extrusion_type"), "Single Mould Extrusion"),
		"slitting_type": get_route_filter_value(doc.get("slitting_requirement"), "None"),
	}

	product_type_filters = [product_type, ""] if product_type else [""]
	for product_type_filter in product_type_filters:
		for printing_type_filter in (filters["printing_type"], ""):
			route_filters = filters.copy()
			route_filters["product_type"] = product_type_filter
			route_filters["printing_type"] = printing_type_filter
			route_name = find_process_route(route_filters)
			if route_name:
				return frappe.get_doc("SmartEdge Process Route", route_name), None

	message = _(
		"No active SmartEdge Process Route found for: {0} / {1} / {2} / {3} / {4}."
	).format(
		product_type or "-",
		filters["printing_type"],
		filters["printing_route"],
		filters["extrusion_type"],
		filters["slitting_type"],
	)
	if throw:
		frappe.throw(message)
	return None, message


def find_process_route(route_filters):
	candidates = frappe.get_all(
		"SmartEdge Process Route",
		filters={
			"enabled": route_filters["enabled"],
			"printing_route": route_filters["printing_route"],
			"extrusion_type": route_filters["extrusion_type"],
			"slitting_type": route_filters["slitting_type"],
		},
		fields=["name", "product_type", "printing_type"],
		order_by="route_name asc",
	)
	for candidate in candidates:
		if get_route_filter_value(candidate.product_type) != route_filters["product_type"]:
			continue
		if get_route_filter_value(candidate.printing_type) != route_filters["printing_type"]:
			continue
		return candidate.name

	return None


def get_route_filter_value(value, fallback=""):
	value = str(value or "").strip()
	return value or fallback


def build_eb_route(configuration):
	route_doc, message = resolve_process_route(frappe._dict(configuration), throw=True)
	if message:
		frappe.throw(message)
	return [(row.operation, row.workstation) for row in sorted(route_doc.get("operations"), key=lambda row: row.sequence)]


def get_route_operations(doc, route_doc):
	rows = []
	warnings = []
	production_quantity = flt(doc.get("production_quantity"))

	if not route_doc:
		return rows, warnings

	for route_operation in sorted(route_doc.get("operations") or [], key=lambda row: row.sequence):
		operation_name = route_operation.operation
		workstation = route_operation.workstation
		planning_category = route_operation.get("planning_category") or get_operation_planning_category(operation_name)
		if not frappe.db.exists("Operation", operation_name):
			warnings.append(_("Missing Operation: {0}").format(operation_name))
			rows.append(
				{
					"sequence": route_operation.sequence,
					"operation": operation_name,
					"planning_category": planning_category or "Other",
					"mandatory": route_operation.mandatory,
				}
			)
			continue

		if not workstation:
			warnings.append(_("No Workstation configured for Operation: {0}").format(operation_name))
		elif not frappe.db.exists("Workstation", workstation):
			warnings.append(_("Configured Workstation does not exist for Operation {0}: {1}").format(operation_name, workstation))

		operation = frappe.db.get_value("Operation", operation_name, ["total_operation_time"], as_dict=True)
		standard_time = flt(operation.total_operation_time if operation else 0)
		if not standard_time:
			warnings.append(_("Standard Time Not Configured for Operation: {0}").format(operation_name))

		rates = get_workstation_rates(workstation)
		total_time_mins = standard_time * production_quantity
		machine_cost = flt(rates.machine_rate) * total_time_mins / 60
		labour_cost = flt(rates.labour_rate) * total_time_mins / 60

		rows.append(
			{
				"sequence": route_operation.sequence,
				"operation": operation_name,
				"workstation": workstation,
				"planning_category": planning_category,
				"standard_time": standard_time,
				"time_uom": "Minutes",
				"planned_rate": flt(rates.machine_rate) + flt(rates.labour_rate),
				"machine_cost": machine_cost,
				"labour_cost": labour_cost,
				"mandatory": route_operation.mandatory,
				"is_qc": 1 if planning_category == "QC" else 0,
				"is_printing": 1 if planning_category == "Printing" else 0,
				"is_slitting": 1 if planning_category == "Slitting" else 0,
				"is_extrusion": 1 if planning_category == "Extrusion" else 0,
			}
		)

	return rows, warnings


def get_workstation_rates(workstation):
	if not workstation or not frappe.db.exists("Workstation", workstation):
		return frappe._dict({"machine_rate": 0, "labour_rate": 0})

	meta = frappe.get_meta("Workstation", cached=True)
	fields = ["hour_rate"]
	if meta.has_field("custom_machine_cost_per_hour"):
		fields.append("custom_machine_cost_per_hour")
	if meta.has_field("custom_labour_cost_per_hour"):
		fields.append("custom_labour_cost_per_hour")

	values = frappe.db.get_value("Workstation", workstation, fields, as_dict=True) or {}
	machine_rate = flt(values.get("custom_machine_cost_per_hour")) if "custom_machine_cost_per_hour" in values else 0
	labour_rate = flt(values.get("custom_labour_cost_per_hour")) if "custom_labour_cost_per_hour" in values else 0
	if not machine_rate and not labour_rate:
		machine_rate = flt(values.get("hour_rate"))

	return frappe._dict({"machine_rate": machine_rate, "labour_rate": labour_rate})


def get_operation_warning(operation_name, workstation, standard_time):
	if not workstation:
		return _("No Workstation configured for Operation: {0}").format(operation_name)
	if not frappe.db.exists("Workstation", workstation):
		return _("Configured Workstation does not exist: {0}").format(workstation)
	if not standard_time:
		return _("Standard Time Not Configured")
	return ""


def get_operation_planning_category(operation_name):
	if frappe.get_meta("Operation", cached=True).has_field("custom_planning_category"):
		return frappe.db.get_value("Operation", operation_name, "custom_planning_category") or "Other"

	operation_text = (operation_name or "").lower()
	if "extrusion" in operation_text:
		return "Extrusion"
	if "printing" in operation_text:
		return "Printing"
	if "slitting" in operation_text:
		return "Slitting"
	if "qc" in operation_text:
		return "QC"
	if "packing" in operation_text:
		return "Packing"
	return "Other"


def calculate_bom_preview(source):
	doc = frappe._dict(source)
	apply_item_configuration_defaults(doc)
	apply_process_defaults(doc)

	weight = calculate_weight(
		doc.get("thickness_mm"),
		doc.get("width_mm"),
		doc.get("weight_factor") or 1.5,
		doc.get("production_quantity"),
	)
	doc.update(weight)

	materials = calculate_component_requirements(doc)
	route_doc, route_warning = resolve_process_route(doc)
	doc["selected_route"] = route_doc.name if route_doc else None
	additional_costs, missing_consumables = calculate_consumable_costs(doc, route_doc)
	operations, route_warnings = get_route_operations(doc, route_doc)
	if route_warning:
		route_warnings.append(route_warning)
	material_total = sum(flt(row.get("amount")) for row in materials)
	additional_cost_total = sum(flt(row.get("total_cost")) for row in additional_costs)
	machine_total = sum(flt(row.get("machine_cost")) for row in operations)
	labour_total = sum(flt(row.get("labour_cost")) for row in operations)
	total_operation_cost = machine_total + labour_total
	total_production_cost = material_total + additional_cost_total + total_operation_cost

	compound_required = next((row for row in materials if row["component_type"] == "Compound"), {})
	shade_required = next((row for row in materials if row["component_type"] == "Base Colour / Shade"), {})
	component_weight_per_meter = get_component_weight_per_meter_kg((compound_required, shade_required))
	difference = flt(weight["weight_per_meter_kg"]) - component_weight_per_meter

	warnings = []
	if abs(difference) > MATERIAL_TOLERANCE:
		warnings.append(
			_(
				"Calculated EB weight is {0} Kg/m. Compound + Shade consumption is {1} Kg/m. Difference: {2} Kg/m."
			).format(
				flt(weight["weight_per_meter_kg"], 6),
				flt(component_weight_per_meter, 6),
				flt(difference, 6),
			)
		)
	for row in materials:
		if flt(row.get("shortage_qty")) < 0:
			warnings.append(
				_("{0} stock shortage: {1} {2}.").format(
					row.get("component_type"),
					flt(abs(row.get("shortage_qty")), 6),
					row.get("uom") or "",
				)
			)
	if missing_consumables:
		warnings.append(_("Missing or disabled SmartEdge Cost Head(s): {0}.").format(", ".join(missing_consumables)))
	warnings.extend(route_warnings)

	status = get_status(doc, difference, materials, missing_consumables, route_warnings)

	return {
		**weight,
		"compound_item": compound_required.get("item_code"),
		"base_colour_item": shade_required.get("item_code"),
		"compound_total_required": compound_required.get("total_quantity", 0),
		"compound_available_qty": compound_required.get("available_stock", 0),
		"compound_shortage_qty": compound_required.get("shortage_qty", 0),
		"shade_total_required": shade_required.get("total_quantity", 0),
		"shade_available_qty": shade_required.get("available_stock", 0),
		"shade_shortage_qty": shade_required.get("shortage_qty", 0),
		"material_weight_status": "Material Weight Matched"
		if abs(difference) <= MATERIAL_TOLERANCE
		else "Material Weight Mismatch",
		"material_weight_difference_kg_per_meter": difference,
		"materials": materials,
		"additional_costs": additional_costs,
		"operations": operations,
		"compound_cost": compound_required.get("amount", 0),
		"shade_cost": shade_required.get("amount", 0),
		"material_cost": material_total,
		"additional_consumable_cost": additional_cost_total,
		"operation_cost": machine_total,
		"labour_cost": labour_total,
		"total_production_cost": total_production_cost,
		"cost_per_meter": total_production_cost / flt(doc.get("production_quantity"))
		if flt(doc.get("production_quantity"))
		else 0,
		"warnings": "\n".join(warnings),
		"status": status,
		"missing_consumables": missing_consumables,
		"selected_route": route_doc.name if route_doc else None,
		"route_summary": get_route_summary(doc, route_doc),
	}


def apply_item_configuration_defaults(doc):
	if not doc.get("finished_item"):
		return

	config = get_finished_item_configuration(doc.get("finished_item"))
	for fieldname, value in config.items():
		if value in (None, ""):
			continue
		if fieldname in {"weight_factor", "printing_route", "extrusion_type", "slitting_requirement"} and doc.get(
			fieldname
		):
			continue
		if fieldname == "source_warehouse" and doc.get(fieldname):
			continue
		if fieldname in {"thickness_mm", "width_mm"} and flt(doc.get(fieldname)):
			continue
		set_doc_value(doc, fieldname, value)


def apply_process_defaults(doc):
	if doc.get("extrusion_type") == "Wide Sheet Extrusion" and doc.get("slitting_requirement") in (
		None,
		"",
		"None",
	):
		set_doc_value(doc, "slitting_requirement", "400 mm Wide Sheet Slitting")
	elif doc.get("extrusion_type") == "Single Mould Extrusion" and not doc.get("slitting_requirement"):
		set_doc_value(doc, "slitting_requirement", "None")


def set_doc_value(doc, fieldname, value):
	setter = getattr(doc, "set", None)
	if callable(setter):
		setter(fieldname, value)
	else:
		doc[fieldname] = value


def get_status(doc, difference, materials, missing_consumables, route_warnings):
	if doc.get("status") in {"Partially Planned", "Planned", "In Production", "Completed", "On Hold", "Cancelled"}:
		return doc.get("status")
	if any(flt(row.get("shortage_qty")) < 0 for row in materials):
		return "Draft"
	if route_warnings:
		return "Draft"
	if abs(difference) > MATERIAL_TOLERANCE:
		return "Draft"
	if missing_consumables:
		return "Draft"
	if doc.get("finished_item") and doc.get("compound") and doc.get("base_colour") and doc.get("selected_route"):
		return "Ready for Planning"
	return "Draft"


def get_route_summary(doc, route_doc):
	if not route_doc:
		return {}

	return {
		"route": route_doc.name,
		"route_name": route_doc.route_name,
		"product_type": route_doc.product_type or doc.get("custom_product_type_design_category"),
		"printing_type": doc.get("printing_type"),
		"printing_route": doc.get("printing_route"),
		"extrusion_type": doc.get("extrusion_type"),
		"slitting_requirement": doc.get("slitting_requirement"),
	}


def apply_preview_to_doc(doc, preview):
	for fieldname in (
		"weight_per_meter_gm",
		"weight_per_meter_kg",
		"total_eb_weight_kg",
		"compound_item",
		"base_colour_item",
		"compound_total_required",
		"compound_available_qty",
		"compound_shortage_qty",
		"shade_total_required",
		"shade_available_qty",
		"shade_shortage_qty",
		"material_weight_status",
		"material_weight_difference_kg_per_meter",
		"compound_cost",
		"shade_cost",
		"material_cost",
		"additional_consumable_cost",
		"operation_cost",
		"labour_cost",
		"total_production_cost",
		"cost_per_meter",
		"warnings",
		"status",
		"selected_route",
	):
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, preview.get(fieldname))

	doc.set("material_preview", [])
	for row in preview.get("materials") or []:
		doc.append("material_preview", row)

	doc.set("additional_costs", [])
	for row in preview.get("additional_costs") or []:
		doc.append("additional_costs", row)

	doc.set("operation_preview", [])
	for row in preview.get("operations") or []:
		doc.append("operation_preview", row)


def validate_ready_for_planning(doc):
	doc.check_permission("write")
	preview = calculate_bom_preview(doc.as_dict())
	apply_preview_to_doc(doc, preview)

	required = {
		"finished_item": _("Finished Product Item"),
		"company": _("Company"),
		"compound": _("Compound"),
		"base_colour": _("Base Colour / Shade"),
		"extrusion_type": _("Extrusion Type"),
	}
	for fieldname, label in required.items():
		if not doc.get(fieldname):
			frappe.throw(_("{0} is required.").format(label))

	for fieldname, label in (
		("production_quantity", _("Production Quantity")),
		("thickness_mm", _("Thickness")),
		("width_mm", _("Width")),
		("weight_factor", _("Weight Factor")),
		("compound_consumption_per_meter", _("Compound Consumption / Meter")),
		("shade_consumption_per_meter", _("Shade Consumption / Meter")),
	):
		if flt(doc.get(fieldname)) <= 0:
			frappe.throw(_("{0} must be greater than zero.").format(label))

	if doc.get("printing_type") != "Solid" and not doc.get("printing_route"):
		frappe.throw(_("Printing Route must be selected for printed products."))

	if not preview.get("selected_route"):
		frappe.throw(preview.get("warnings") or _("No manufacturing route configured for this combination."))

	if abs(flt(doc.material_weight_difference_kg_per_meter)) > MATERIAL_TOLERANCE and not doc.allow_weight_mismatch:
		frappe.throw(_("Material weight mismatch must be corrected or acknowledged before sending to planning."))

	for row in doc.get("operation_preview") or []:
		if not row.operation or not frappe.db.exists("Operation", row.operation):
			frappe.throw(_("Missing Operation master: {0}").format(row.operation or ""))
		if not row.workstation or not frappe.db.exists("Workstation", row.workstation):
			frappe.throw(_("Missing Workstation for Operation: {0}").format(row.operation))

	if preview.get("missing_consumables"):
		frappe.throw(
			_("Missing or disabled SmartEdge Cost Head(s): {0}.").format(
				", ".join(preview.get("missing_consumables"))
			)
		)

	if not doc.get("additional_costs"):
		frappe.throw(_("No SmartEdge Cost Heads are configured for the selected route."))

	return preview


def send_generator_to_planning(doc):
	preview = validate_ready_for_planning(doc)
	doc.status = "Ready for Planning"
	doc.save()
	entries = create_planning_entries(doc)
	return {"status": doc.status, "entries": entries, **preview}


def create_planning_entries(doc):
	if not frappe.has_permission("SmartEdge Production Plan Entry", "create"):
		frappe.throw(_("Not permitted to create SmartEdge Production Plan Entry."), frappe.PermissionError)

	entries = []
	for operation in doc.get("operation_preview") or []:
		existing = frappe.db.get_value(
			"SmartEdge Production Plan Entry",
			{
				"final_eb_bom_generator": doc.name,
				"operation_row": operation.name,
				"status": ["!=", "Cancelled"],
			},
			"name",
		)
		if existing:
			entries.append(existing)
			continue

		entry = frappe.new_doc("SmartEdge Production Plan Entry")
		entry.final_eb_bom_generator = doc.name
		entry.operation_row = operation.name
		entry.operation = operation.operation
		entry.workstation = operation.workstation
		entry.planning_category = operation.planning_category or "Other"
		entry.sequence = operation.sequence
		entry.production_quantity = doc.production_quantity
		entry.planned_meter = doc.production_quantity
		entry.planned_kg = doc.total_eb_weight_kg
		entry.status = "Pending"
		entry.priority = "Normal"
		entry.insert(ignore_permissions=True)
		entries.append(entry.name)

	return entries


@frappe.whitelist()
def get_finished_item_details(finished_item):
	return get_finished_item_configuration(finished_item)


@frappe.whitelist()
def preview_generator(doc):
	if isinstance(doc, str):
		doc = frappe.parse_json(doc)
	return calculate_bom_preview(doc or {})


@frappe.whitelist()
def send_to_planning(name):
	doc = frappe.get_doc("Final EB BOM Generator", name)
	return send_generator_to_planning(doc)
