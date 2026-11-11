"""Sales Order list-view adapter for the existing Final EB BOM workflow."""

import json

import frappe
from frappe import _
from frappe.utils import flt

from smart_edge_custom.manufacturing.sales_order_planning import (
	allocate_finished_stock,
	build_manufacturing_overview,
	create_final_eb_bom_generator,
	get_valid_sales_order,
)


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
	priority_rank = {"High": 0, "Medium": 1, "Low": 2}
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
			row["_priority"] = priority_rank.get(order.get("priority"), 9)
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
def generate_fg_and_bom(requirements):
	frappe.has_permission("Final EB BOM Generator", "create", throw=True)
	try:
		requirements = json.loads(requirements) if isinstance(requirements, str) else requirements
	except (TypeError, ValueError):
		frappe.throw(_("Invalid Sales Order selection."))
	if not isinstance(requirements, list) or not requirements:
		frappe.throw(_("Select at least one pending Sales Order item."))

	results = []
	for requirement in requirements:
		try:
			order = get_valid_sales_order(requirement.get("sales_order"))
			# Revalidate the line and shortage server-side; client quantities are ignored.
			overview = build_manufacturing_overview(order)
			row = next((item for item in overview["items"] if item["sales_order_item"] == requirement.get("sales_order_item")), None)
			if not row or flt(row.get("manufacturing_shortage")) <= 0:
				frappe.throw(_("No pending manufacturing requirement remains for this Sales Order item."))
			created = create_final_eb_bom_generator(
				order.name, row["sales_order_item"], flt(row["manufacturing_shortage"])
			)
			results.append({"sales_order": order.name, "sales_order_item": row["sales_order_item"], **created, "message": _("FG Generator created; existing Final EB BOM workflow retained.")})
		except Exception as exc:
			results.append({"sales_order": requirement.get("sales_order"), "sales_order_item": requirement.get("sales_order_item"), "error": str(exc)})
	return results


def _parse_names(value):
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			value = [value]
	return [str(name) for name in value if name]
