import frappe

from smart_edge_custom.manufacturing.sales_order_planning_status import (
	ensure_planning_status_field,
	recalculate_all_sales_order_planning_status,
)


def execute():
	if not frappe.db.exists("DocType", "Sales Order"):
		return

	ensure_planning_status_field()
	recalculate_all_sales_order_planning_status()
