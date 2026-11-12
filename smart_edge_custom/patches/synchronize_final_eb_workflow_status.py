import frappe

from smart_edge_custom.manufacturing.final_bom_generator import get_generator_lifecycle_status
from smart_edge_custom.manufacturing.material_production import refresh_material_readiness


def execute():
	"""Preserve legacy lifecycle status and reconcile the visible status once."""
	if not frappe.db.has_column("Final EB BOM Generator", "production_status"):
		return

	for row in frappe.get_all(
		"Final EB BOM Generator",
		fields=["name", "status", "production_status"],
		limit_page_length=0,
	):
		lifecycle_status = get_generator_lifecycle_status(row)
		if not row.production_status:
			frappe.db.set_value(
				"Final EB BOM Generator",
				row.name,
				"production_status",
				lifecycle_status,
				update_modified=False,
			)
		refresh_material_readiness(row.name)
