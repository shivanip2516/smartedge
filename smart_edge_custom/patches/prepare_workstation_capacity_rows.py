import frappe


def execute():
	"""Validate legacy capacity rows before manual consolidation.

	This patch is intentionally non-destructive: it reports conflicting
	workstation/unit/size rates and aborts without rewriting any records.
	"""
	rows = frappe.get_all(
		"SmartEdge Workstation Capacity Rate",
		filters={"enabled": 1},
		fields=["name", "workstation", "capacity_rate", "capacity_uom", "product_size"],
		limit_page_length=5000,
	)
	groups = {}
	for row in rows:
		key = (row.workstation, row.product_size or "", row.capacity_uom)
		groups.setdefault(key, []).append(row)
	conflicts = []
	for key, candidates in groups.items():
		rates = {str(row.capacity_rate) for row in candidates}
		if len(rates) > 1:
			conflicts.append(f"{key}: {', '.join(row.name for row in candidates)}")
	if conflicts:
		frappe.throw(
			"Capacity migration stopped. Resolve conflicting rates before consolidation: "
			+ "; ".join(conflicts)
		)
	for row in rows:
		if not row.product_size:
			continue
		parent = frappe.get_doc("SmartEdge Workstation Capacity Rate", row.name)
		existing = [child for child in parent.get("capacity_rows") or [] if child.product_size == row.product_size]
		if existing:
			if any(str(child.capacity_rate) != str(row.capacity_rate) or child.capacity_uom != row.capacity_uom for child in existing):
				frappe.throw(f"Capacity migration conflict for {row.name} and Product Size {row.product_size}.")
			continue
		parent.append("capacity_rows", {
			"product_size": row.product_size,
			"capacity_rate": row.capacity_rate,
			"capacity_uom": row.capacity_uom,
		})
		parent.save(ignore_permissions=True)
	frappe.logger("smart_edge_custom").info(
		"Workstation capacity migration preflight found %s compatible legacy rows; no records changed.", len(rows)
	)
