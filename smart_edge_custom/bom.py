import frappe
from frappe import _
from frappe.query_builder import Field
from frappe.query_builder.functions import IfNull
from frappe.utils import parse_json, today


COMPOUND_ITEM_GROUP = "Compounds"
BASE_COLOUR_ITEM_GROUP = "Base Colour"
PROCESS_ITEM_GROUPS = {
	"Extrusion": ["Compounds", "Pigments"],
	"Extrusion Online Printing": ["Compounds", "Pigments"],
	"Offline Printing": ["Base Colour"],
}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_process_items(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe._dict(parse_json(filters) or {})
	process = filters.get("process")
	allowed_item_groups = PROCESS_ITEM_GROUPS.get(process)

	if not allowed_item_groups:
		from erpnext.manufacturing.doctype.bom.bom import item_query

		return item_query(
			doctype,
			txt,
			searchfield,
			start,
			page_len,
			{
				"include_item_in_manufacturing": 1,
				"is_fixed_asset": 0,
			},
		)

	meta = frappe.get_meta("Item", cached=True)
	searchfields = meta.get_search_fields()

	order_by = "idx desc, name, item_name"
	fields = ["name", "item_name", "item_group", "description"]
	fields.extend([field for field in searchfields if field not in ["name", "item_group", "description"]])

	if not searchfields:
		searchfields = ["name"]

	query_filters = [
		["disabled", "=", 0],
		[IfNull(Field("end_of_life"), "3099-12-31"), ">", today()],
		["include_item_in_manufacturing", "=", 1],
		["is_fixed_asset", "=", 0],
		["item_group", "in", allowed_item_groups],
	]

	or_cond_filters = {}
	if txt:
		for fieldname in searchfields:
			or_cond_filters[fieldname] = ("like", f"%{txt}%")

		barcodes = frappe.get_all(
			"Item Barcode",
			fields=["parent as item_code"],
			filters={"barcode": ("like", f"%{txt}%")},
			distinct=True,
		)

		barcodes = [row.item_code for row in barcodes]
		if barcodes:
			or_cond_filters["name"] = ("in", barcodes)

	if filters.get("item_code"):
		has_variants = frappe.get_cached_value("Item", filters.get("item_code"), "has_variants")
		if not has_variants:
			query_filters.append(["has_variants", "=", 0])

	return frappe.get_list(
		"Item",
		fields=fields,
		filters=query_filters,
		or_filters=or_cond_filters,
		order_by=order_by,
		limit_start=start,
		limit_page_length=page_len,
		as_list=1,
	)


@frappe.whitelist()
def get_compound_recipe(item_code):
	if not item_code:
		return {"is_compound": False, "rows": []}

	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if item_group != COMPOUND_ITEM_GROUP:
		return {"is_compound": False, "rows": []}

	compound = frappe.db.get_value("Compound", {"compound_name": item_code}, "name")
	if not compound:
		return {"is_compound": True, "recipe_found": False, "rows": []}

	rows = []
	for row in frappe.get_all(
		"Compound Additive",
		filters={"parent": compound, "parenttype": "Compound"},
		fields=["additive", "quantity_kg", "uom"],
		order_by="idx asc",
	):
		if not row.additive or not frappe.db.exists("Item", row.additive):
			frappe.throw(_("Invalid Additive Item in Compound recipe: {0}").format(row.additive or ""))

		rows.append(
			{
				"item_code": row.additive,
				"qty": row.quantity_kg,
				"uom": row.uom or frappe.db.get_value("Item", row.additive, "stock_uom"),
			}
		)

	return {"is_compound": True, "recipe_found": True, "compound": compound, "rows": rows}


@frappe.whitelist()
def get_base_colour_recipe(item_code):
	if not item_code:
		return {"is_base_colour": False, "rows": []}

	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if item_group != BASE_COLOUR_ITEM_GROUP:
		return {"is_base_colour": False, "rows": []}

	base_colour = frappe.db.get_value("Base Colour", {"colour_name": item_code}, "name")
	if not base_colour:
		return {"is_base_colour": True, "recipe_found": False, "rows": []}

	rows = []
	for row in frappe.get_all(
		"Base Colour Pigment",
		filters={"parent": base_colour, "parenttype": "Base Colour"},
		fields=["pigment_item", "quantity_kg", "uom"],
		order_by="idx asc",
	):
		if not row.pigment_item or not frappe.db.exists("Item", row.pigment_item):
			frappe.throw(_("Invalid Pigment Item in Base Colour recipe: {0}").format(row.pigment_item or ""))

		rows.append(
			{
				"item_code": row.pigment_item,
				"qty": row.quantity_kg,
				"uom": row.uom or frappe.db.get_value("Item", row.pigment_item, "stock_uom"),
			}
		)

	return {"is_base_colour": True, "recipe_found": True, "base_colour": base_colour, "rows": rows}
