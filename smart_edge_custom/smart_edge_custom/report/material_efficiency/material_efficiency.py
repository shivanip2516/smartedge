import frappe
from frappe import _
from frappe.utils import flt

from smart_edge_custom.smart_edge_custom.report.production_reporting import pct, validate


def execute(filters=None):
	filters = validate(filters, True)
	groups = {}

	for job in _get_jobs(filters):
		generator = _get_generator(job.get("final_eb_bom_generator"))
		if filters.get("item") and (not generator or generator.finished_item != filters.item):
			continue

		entry = _get_plan_entry(job.get("plan_entry"))
		is_material_batch = bool(entry and entry.get("plan_type") == "Required Material")
		key = (
			generator.finished_item if generator else None,
			job.operation,
			job.workstation,
		)
		row = groups.setdefault(key, _new_group(*key))

		if is_material_batch:
			standard_batch_kg = _quantity_in_kg(
				entry.production_quantity,
				entry.quantity_uom,
				entry.material_item,
			)
			if standard_batch_kg is not None:
				if row["standard_material_consumption"] is None:
					row["standard_material_consumption"] = 0.0
				row["standard_material_consumption"] += standard_batch_kg
			row["meter_output"] = None
			row["standard_kg_per_meter"] = None
			row["actual_kg_per_meter"] = None
		else:
			meter_output = flt(job.actual_qty_meter)
			row["meter_output"] += meter_output
			if generator and generator.weight_per_meter_kg:
				row["standard_kg_per_meter"] = flt(generator.weight_per_meter_kg)
				if row["standard_material_consumption"] is None:
					row["standard_material_consumption"] = 0.0
				row["standard_material_consumption"] += flt(generator.weight_per_meter_kg) * meter_output

		actual_consumption = _stock_entry_input_kg(job.get("manufacturing_stock_entry"))
		if actual_consumption is not None:
			row["actual_material_consumption"] += actual_consumption
			row["has_actual_consumption"] = True

		row["good_output_quantity"] += flt(job.good_qty or job.actual_qty_meter)

	rows = []
	for row in groups.values():
		if row["meter_output"] and row["has_actual_consumption"]:
			row["actual_kg_per_meter"] = row["actual_material_consumption"] / row["meter_output"]
		else:
			row["actual_kg_per_meter"] = None

		if not row["has_actual_consumption"]:
			row["actual_material_consumption"] = None
		if row["standard_material_consumption"] is None or row["actual_material_consumption"] is None:
			row["material_variance"] = None
			row["material_variance_percent"] = None
		else:
			row["material_variance"] = row["actual_material_consumption"] - row["standard_material_consumption"]
			row["material_variance_percent"] = pct(
				row["material_variance"], row["standard_material_consumption"]
			)

		# Scrap and quality quantities have operation-specific UOMs and are not
		# sufficient to derive a comparable mass yield without an approved basis.
		row.pop("meter_output", None)
		row.pop("has_actual_consumption", None)
		rows.append(frappe._dict(row))

	columns = [
		{"label": _("Finished Item"), "fieldname": "finished_item", "fieldtype": "Link", "options": "Item"},
		{"label": _("Operation"), "fieldname": "operation", "fieldtype": "Link", "options": "Operation"},
		{"label": _("Workstation"), "fieldname": "workstation", "fieldtype": "Link", "options": "Workstation"},
		{"label": _("Standard Kg/Meter"), "fieldname": "standard_kg_per_meter", "fieldtype": "Float"},
		{"label": _("Actual Kg/Meter"), "fieldname": "actual_kg_per_meter", "fieldtype": "Float"},
		{"label": _("Standard Consumption (Kg)"), "fieldname": "standard_material_consumption", "fieldtype": "Float"},
		{"label": _("Actual Consumption (Kg)"), "fieldname": "actual_material_consumption", "fieldtype": "Float"},
		{"label": _("Material Variance (Kg)"), "fieldname": "material_variance", "fieldtype": "Float"},
		{"label": _("Material Variance %"), "fieldname": "material_variance_percent", "fieldtype": "Percent"},
		{"label": _("Yield %"), "fieldname": "yield_percent", "fieldtype": "Percent"},
		{"label": _("Process Loss %"), "fieldname": "process_loss_percent", "fieldtype": "Percent"},
	]
	return columns, rows, None, None, None


def _get_jobs(filters):
	conditions = [
		["SmartEdge Production Job", "actual_start", ">=", filters.from_date],
		["SmartEdge Production Job", "actual_start", "<", frappe.utils.add_days(filters.to_date, 1)],
		["SmartEdge Production Job", "status", "=", "Completed"],
		["SmartEdge Production Job", "company", "=", filters.company],
	]
	for fieldname in ("workstation", "operation"):
		if filters.get(fieldname):
			conditions.append(["SmartEdge Production Job", fieldname, "=", filters[fieldname]])

	return frappe.get_list(
		"SmartEdge Production Job",
		filters=conditions,
		fields=[
			"name", "plan_entry", "final_eb_bom_generator", "operation", "workstation",
			"actual_qty_meter", "good_qty", "manufacturing_stock_entry",
		],
		limit_page_length=0,
		order_by="actual_start asc, name asc",
	)


def _get_generator(name):
	if not name:
		return None
	return frappe.db.get_value(
		"Final EB BOM Generator", name, ["finished_item", "weight_per_meter_kg"], as_dict=True
	)


def _get_plan_entry(name):
	if not name:
		return None
	return frappe.db.get_value(
		"SmartEdge Production Plan Entry", name,
		["plan_type", "production_quantity", "quantity_uom", "material_item"], as_dict=True
	)


def _stock_entry_input_kg(stock_entry):
	if not stock_entry or frappe.db.get_value("Stock Entry", stock_entry, "docstatus") != 1:
		return None

	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={"parent": stock_entry, "parenttype": "Stock Entry", "s_warehouse": ["is", "set"]},
		fields=["item_code", "qty", "transfer_qty", "stock_uom"],
	)
	if not rows:
		return None

	from erpnext.stock.doctype.item.item import get_uom_conv_factor

	total = 0.0
	for row in rows:
		stock_uom = row.stock_uom or frappe.db.get_value("Item", row.item_code, "stock_uom")
		if not stock_uom:
			return None
		kg_to_stock = get_uom_conv_factor("Kg", stock_uom)
		if kg_to_stock is None:
			return None
		total += flt(row.transfer_qty or row.qty) / flt(kg_to_stock)
	return total


def _quantity_in_kg(quantity, uom, item_code):
	if quantity is None or not uom or not item_code:
		return None
	from erpnext.stock.get_item_details import get_conversion_factor
	from erpnext.stock.doctype.item.item import get_uom_conv_factor

	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	if not stock_uom:
		return None
	factor = get_conversion_factor(item_code, uom).get("conversion_factor")
	kg_to_stock = get_uom_conv_factor("Kg", stock_uom)
	if factor is None or kg_to_stock is None:
		return None
	return flt(quantity) * flt(factor) / flt(kg_to_stock)


def _new_group(finished_item, operation, workstation):
	return {
		"finished_item": finished_item,
		"operation": operation,
		"workstation": workstation,
		"standard_kg_per_meter": None,
		"actual_kg_per_meter": None,
		"standard_material_consumption": None,
		"actual_material_consumption": 0.0,
		"material_variance": None,
		"material_variance_percent": None,
		"good_output_quantity": 0.0,
		"yield_percent": None,
		"process_loss_percent": None,
		"meter_output": 0.0,
		"has_actual_consumption": False,
	}
