from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt, parse_json

from smart_edge_custom.base_colour import get_conversion_factor

from smart_edge_custom.manufacturing.batch_manufacturing import (
	get_item_batch_details,
)
from smart_edge_custom.manufacturing.batch_manufacturing import (
	validate_bom as validate_legacy_batch_bom,
)


COMPONENT_ITEM_GROUPS = {
	"Compounds": "Additives",
	"Base Colour": "Pigments",
}


def get_allowed_component_group(item_code):
	if not item_code:
		return None
	return COMPONENT_ITEM_GROUPS.get(frappe.db.get_value("Item", item_code, "item_group"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_bom_items(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.manufacturing.doctype.bom.bom import item_query

	filters = frappe._dict(parse_json(filters) or {})
	allowed_group = get_allowed_component_group(filters.get("parent_item"))
	query_filters = {"include_item_in_manufacturing": 1, "is_fixed_asset": 0}
	if allowed_group:
		# Equality deliberately excludes descendant Item Groups.
		query_filters["item_group"] = allowed_group
	return item_query(doctype, txt, searchfield, start, page_len, query_filters)


def validate_bom(doc, method=None):
	allowed_group = get_allowed_component_group(doc.item)
	if not allowed_group:
		validate_legacy_batch_bom(doc, method)
		return

	item_codes = {row.item_code for row in doc.get("items", []) if row.item_code}
	groups = {}
	if item_codes:
		groups = dict(frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes)]},
			fields=["name", "item_group"],
			as_list=True,
		))
	invalid = [
		_("Row {0}: {1}").format(row.idx, row.item_code)
		for row in doc.get("items", [])
		if row.item_code and groups.get(row.item_code) != allowed_group
	]
	if invalid:
		frappe.throw(
			_("BOM for Item {0} allows only components in Item Group {1}. Invalid items: {2}").format(
				doc.item, allowed_group, "; ".join(invalid)
			)
		)

	# Keep the one-batch validation independent of legacy recipe masters.
	details = get_bom_batch_details(doc.item)
	if details["is_batch_manufacturing_item"] and cint(doc.get("custom_batch")) != 1:
		frappe.throw(_("Batch must be 1 for a batch-controlled Compound or Base Colour BOM."))


@frappe.whitelist()
def get_bom_batch_details(item_code):
	if not get_allowed_component_group(item_code):
		return get_item_batch_details(item_code)

	item = frappe.db.get_value("Item", item_code, ["has_batch_no", "stock_uom"], as_dict=True)
	return {
		"has_batch_no": bool(item.has_batch_no),
		"is_batch_manufacturing_item": bool(item.has_batch_no),
		"output_qty": 0,
		"stock_uom": item.stock_uom,
	}


@frappe.whitelist()
def get_bom_quantity(item_code, items=None):
	"""Total recipe rows in the output Item's stock UOM, using existing conversions."""
	if not get_allowed_component_group(item_code):
		return None

	output_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	total = Decimal("0")
	for row in parse_json(items) or []:
		if not row.get("item_code") or not row.get("qty"):
			continue
		stock_uom = frappe.db.get_value("Item", row["item_code"], "stock_uom")
		row_uom = row.get("uom") or stock_uom
		# BOM's row factor converts its transaction UOM to its Item stock UOM.
		# The existing recipe conversion then converts stock UOM to output UOM.
		row_factor = row.get("conversion_factor") if row_uom != stock_uom else 1
		if not row_factor:
			row_factor = get_conversion_factor(row_uom, stock_uom)
		total += (
			Decimal(str(flt(row.get("qty"))))
			* Decimal(str(row_factor))
			* Decimal(str(get_conversion_factor(stock_uom, output_uom)))
		)
	return flt(total, frappe.get_precision("BOM", "quantity"))


def set_recipe_quantity(doc, method=None):
	# Run before core BOM validation/costing, never as a submitted-document update.
	total = get_bom_quantity(doc.item, [row.as_dict() for row in doc.get("items", [])])
	if total is not None:
		doc.quantity = flt(total, doc.precision("quantity"))
