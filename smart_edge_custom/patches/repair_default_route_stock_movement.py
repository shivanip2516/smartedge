"""Repair only untouched default route rows created before stock mapping existed."""
import frappe


def execute():
	from smart_edge_custom.setup import DEFAULT_ROUTES
	default_route_names = [route["route_name"] for route in DEFAULT_ROUTES]
	rows = frappe.get_all(
		"SmartEdge Route Operation",
		filters={"parent": ["in", default_route_names], "stock_movement_type": "Non-Stock Operation"},
		fields=["name", "operation", "sequence", "parent"],
		limit_page_length=0,
	)
	for row in rows:
		text = (row.operation or "").lower()
		if row.sequence == 1 and ("extrusion" in text or "printing" in text):
			movement = "Stockable WIP Output"
		elif any(token in text for token in ("extrusion", "printing", "slitting", "qc")):
			movement = "WIP Transfer / Processing"
		else:
			continue
		frappe.db.set_value("SmartEdge Route Operation", row.name, "stock_movement_type", movement, update_modified=False)
	frappe.clear_cache(doctype="SmartEdge Process Route")
