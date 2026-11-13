"""Sales Order list-view adapter for the existing Final EB BOM workflow."""

import json

import frappe
from frappe import _
from frappe.utils import flt

from smart_edge_custom.manufacturing.sales_order_planning import (
	allocate_finished_stock,
	build_manufacturing_overview,
	build_sales_order_reference,
	create_final_eb_bom_generator_from_sources,
	get_valid_sales_order,
	lock_sales_orders,
	SALES_ORDER_REFERENCE_DOCTYPE,
)

PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


@frappe.whitelist()
def get_pending_fg_requirements(sales_orders=None, start=0, page_length=100):
	frappe.has_permission("Sales Order", "read", throw=True)
	selected = _parse_names(sales_orders)
	if selected:
		order_names = selected
	else:
		order_names = frappe.get_all(
			"Sales Order",
			filters={"docstatus": 1, "status": ["not in", ["Closed", "Cancelled"]]},
			pluck="name", order_by="transaction_date desc, name desc",
			limit_start=max(int(start), 0), limit_page_length=min(max(int(page_length), 1), 500),
		)
	rows = []
	for order_name in order_names:
		order = get_valid_sales_order(order_name)
		overview = build_manufacturing_overview(order)
		for row in overview["items"]:
			if flt(row.get("outstanding_qty")) <= 0:
				continue
			row["_order_name"] = order.name
			row["_customer"] = order.customer
			row["_customer_name"] = frappe.db.get_value("Customer", order.customer, "customer_name") or order.customer
			row["_delivery_date"] = row.get("delivery_date") or order.delivery_date
			row["_priority"] = PRIORITY_RANK.get(order.get("priority"), 9)
			rows.append(row)
	# Re-run the existing stock-aware allocator once across all orders. This
	# prevents the same shared FG stock from being allocated independently per
	# Sales Order when the popup spans multiple orders.
	for rank, row in enumerate(sorted(rows, key=lambda value: (
		value.get("_delivery_date") or "9999-12-31", value.get("_priority", 9), value.get("_order_name"), value.get("idx", 0)
	))):
		row["idx"] = rank
	allocate_finished_stock(rows)
	result = []
	for row in rows:
		pending = flt(row.get("manufacturing_shortage"))
		if pending <= 0:
			continue
		order_name = row.pop("_order_name")
		customer = row.pop("_customer")
		customer_name = row.pop("_customer_name")
		row.pop("_delivery_date", None)
		row.pop("_priority", None)
		result.append({
			"sales_order": order_name,
			"sales_order_item": row["sales_order_item"],
			"customer": customer,
			"customer_name": customer_name,
			"item_code": row["item_code"],
			"item_name": row["item_name"],
			"pending_qty": pending,
			"manufacturing_qty": pending,
			"uom": row["stock_uom"],
			"delivery_date": row.get("delivery_date"),
		})
	return result


@frappe.whitelist()
def preview_fg_and_bom(requirements):
	frappe.has_permission("Final EB BOM Generator", "create", throw=True)
	groups = build_generation_groups(_parse_requirements(requirements))
	return [summarize_generation_group(group) for group in groups]


@frappe.whitelist()
def generate_fg_and_bom(requirements):
	frappe.has_permission("Final EB BOM Generator", "create", throw=True)
	requirements = _parse_requirements(requirements)
	groups = build_generation_groups(requirements, lock=True)
	results = []
	for group in groups:
		created = create_final_eb_bom_generator_from_sources(group["sources"])
		summary = summarize_generation_group(group)
		formatted_quantity = f"{summary['total_production_quantity']:.6f}".rstrip("0").rstrip(".")
		results.append(
			{
				"name": created.name,
				"quantity": flt(created.required_production_quantity),
				"stock_uom": created.stock_uom or summary["production_uom"],
				"finished_item": summary["finished_item"],
				"sales_orders": summary["sales_orders"],
				"sales_order_items": summary["sales_order_items"],
				"message": _("FG Generator created for {0} Sales Order(s): {1} {2}.").format(
					summary["sales_order_count"],
					formatted_quantity,
					summary["production_uom"],
				),
			}
		)
	return results


def build_generation_groups(requirements, lock=False):
	if not requirements:
		frappe.throw(_("Select at least one pending Sales Order item."))

	selected = []
	seen = set()
	for requirement in requirements:
		key = (requirement.get("sales_order"), requirement.get("sales_order_item"))
		if not all(key) or key in seen:
			continue
		seen.add(key)
		selected.append(key)
	if not selected:
		frappe.throw(_("Select at least one pending Sales Order item."))

	order_names = sorted({sales_order for sales_order, _ in selected})
	if lock:
		lock_sales_orders(order_names)
	orders = {name: get_valid_sales_order(name) for name in order_names}
	rows = []
	for order in orders.values():
		overview = build_manufacturing_overview(order)
		for row in overview["items"]:
			row = dict(row)
			row["_order_name"] = order.name
			rows.append(row)

	# Match the pending-requirements endpoint's stock-aware allocation across all
	# selected orders before resolving the requested rows.
	for index, row in enumerate(
		sorted(
			rows,
			key=lambda value: (
				value.get("delivery_date") or "9999-12-31",
				PRIORITY_RANK.get(orders[value["_order_name"]].get("priority"), 9),
				value["_order_name"],
				value.get("idx", 0),
			),
		)
	):
		row["idx"] = index
	allocate_finished_stock(rows)
	rows_by_source = {(row["_order_name"], row["sales_order_item"]): row for row in rows}

	sources = []
	for sales_order, sales_order_item in selected:
		row = rows_by_source.get((sales_order, sales_order_item))
		if not row or flt(row.get("manufacturing_shortage")) <= 0:
			frappe.throw(
				_("No pending manufacturing requirement remains for Sales Order Item {0}.").format(
					sales_order_item
				)
			)
		sources.append(build_sales_order_reference(orders[sales_order], row))

	grouped = {}
	for source in sources:
		key = (source["company"], source["finished_item"], source["production_uom"])
		grouped.setdefault(key, []).append(source)

	groups = list(grouped.values())
	meta = frappe.get_meta("Final EB BOM Generator", cached=True)
	traceability_available = meta.has_field("sales_order_references") and frappe.db.exists(
		"DocType", SALES_ORDER_REFERENCE_DOCTYPE
	)
	if any(len(group) > 1 for group in groups) and not traceability_available:
		frappe.throw(
			_("Final EB BOM traceability is not installed. Run the app migration before consolidating Sales Orders.")
		)
	return [{"sources": group} for group in groups]


def summarize_generation_group(group):
	sources = group["sources"]
	first = sources[0]
	return {
		"finished_item": first["finished_item"],
		"finished_item_name": first.get("finished_item_name"),
		"sales_orders": sorted({source["sales_order"] for source in sources}),
		"sales_order_items": [source["sales_order_item"] for source in sources],
		"sales_order_count": len({source["sales_order"] for source in sources}),
		"total_production_quantity": flt(
			sum(flt(source["converted_production_quantity"]) for source in sources)
		),
		"production_uom": first["production_uom"],
	}


def _parse_requirements(requirements):
	try:
		requirements = json.loads(requirements) if isinstance(requirements, str) else requirements
	except (TypeError, ValueError):
		frappe.throw(_("Invalid Sales Order selection."))
	if not isinstance(requirements, list):
		frappe.throw(_("Invalid Sales Order selection."))
	return requirements


def _parse_names(value):
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			value = [value]
	return [str(name) for name in value if name]
