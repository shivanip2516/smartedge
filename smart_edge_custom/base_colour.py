import re

import frappe
from frappe import _
from frappe.utils import flt

BASE_COLOUR_DOCTYPE = "Base Colour"
COLOUR_NAME_FIELD = "colour_name"
BASE_COLOUR_ITEM_GROUP = "Base Colour"
PIGMENT_ITEM_GROUP = "Pigments"


def validate_base_colour(doc, method=None):
	doc.weightage = validate_weightage(doc.get("weightage"))
	validate_base_colour_item(doc.get(COLOUR_NAME_FIELD))
	validate_pigment_rows(doc)


def validate_weightage(weightage):
	if weightage in (None, ""):
		frappe.throw(_("Weightage must be an integer between 1 and 10."))

	if isinstance(weightage, bool):
		frappe.throw(_("Weightage must be an integer between 1 and 10."))

	if isinstance(weightage, str):
		weightage = weightage.strip()
		if not re.fullmatch(r"\d+", weightage):
			frappe.throw(_("Weightage must be an integer between 1 and 10."))

	if isinstance(weightage, float) and not weightage.is_integer():
		frappe.throw(_("Weightage must be an integer between 1 and 10."))

	try:
		weightage = int(weightage)
	except (TypeError, ValueError):
		frappe.throw(_("Weightage must be an integer between 1 and 10."))

	if weightage < 1 or weightage > 10:
		frappe.throw(_("Weightage must be an integer between 1 and 10."))

	return weightage


def validate_colour_name(colour_name):
	if not (colour_name or "").strip():
		frappe.throw(_("Base Colour Name is required."))

	colour_name = colour_name.strip()
	validate_base_colour_item(colour_name)
	return colour_name


def validate_base_colour_item(item_code):
	if not item_code:
		return

	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if item_group != BASE_COLOUR_ITEM_GROUP:
		frappe.throw(_('Selected Base Colour Item must belong to Item Group "Base Colour".'))


def validate_pigment_rows(doc):
	total = 0
	for row in doc.get("pigments") or []:
		if not row.pigment_item:
			frappe.throw(_("Pigment Item is required in every pigment row."))

		item = frappe.db.get_value("Item", row.pigment_item, ["item_group", "stock_uom"], as_dict=True)
		if not item or item.item_group != PIGMENT_ITEM_GROUP:
			frappe.throw(_('Selected Pigment Item must belong to Item Group "Pigments".'))

		row.uom = item.stock_uom
		row.quantity_kg = validate_quantity(row.quantity_kg)
		total += row.quantity_kg

	doc.total_kg = total


def validate_quantity(quantity):
	if quantity in (None, "") or isinstance(quantity, bool):
		frappe.throw(_("Quantity must be greater than zero."))

	quantity = flt(quantity)
	if quantity <= 0:
		frappe.throw(_("Quantity must be greater than zero."))

	return quantity


def parse_pigment_rows(rows):
	if isinstance(rows, str):
		rows = frappe.parse_json(rows or "[]")

	return rows or []


def set_pigment_rows(doc, rows):
	doc.set("pigments", [])
	for row in parse_pigment_rows(rows):
		doc.append(
			"pigments",
			{
				"pigment_item": (row.get("pigment_item") or "").strip(),
				"uom": row.get("uom"),
				"quantity_kg": row.get("quantity_kg"),
			},
		)


@frappe.whitelist()
def get_base_colours(search=None, start=0, page_length=50):
	if not frappe.has_permission(BASE_COLOUR_DOCTYPE, "read"):
		frappe.throw(_("Not permitted to read Base Colour"), frappe.PermissionError)

	filters = {}
	if search:
		filters[COLOUR_NAME_FIELD] = ["like", f"%{search}%"]

	rows = frappe.get_list(
		BASE_COLOUR_DOCTYPE,
		filters=filters,
		fields=["name", COLOUR_NAME_FIELD, "weightage", "total_kg"],
		order_by=f"weightage asc, {COLOUR_NAME_FIELD} asc",
		start=int(start or 0),
		page_length=int(page_length or 50),
	)

	for row in rows:
		row.pigments = frappe.get_all(
			"Base Colour Pigment",
			filters={"parent": row.name, "parenttype": BASE_COLOUR_DOCTYPE},
			fields=["pigment_item", "uom", "quantity_kg"],
			order_by="idx asc",
		)

	return rows


@frappe.whitelist()
def create_base_colour(colour_name, weightage, pigments=None):
	colour_name = validate_colour_name(colour_name)
	weightage = validate_weightage(weightage)

	doc = frappe.new_doc(BASE_COLOUR_DOCTYPE)
	doc.set(COLOUR_NAME_FIELD, colour_name)
	doc.weightage = weightage
	set_pigment_rows(doc, pigments)
	doc.insert()

	return doc.name


@frappe.whitelist()
def update_base_colour(name, colour_name, weightage, pigments=None):
	colour_name = validate_colour_name(colour_name)
	weightage = validate_weightage(weightage)

	doc = frappe.get_doc(BASE_COLOUR_DOCTYPE, name)
	doc.check_permission("write")

	if doc.meta.autoname == f"field:{COLOUR_NAME_FIELD}" and doc.name != colour_name:
		name = frappe.rename_doc(BASE_COLOUR_DOCTYPE, doc.name, colour_name)
		doc = frappe.get_doc(BASE_COLOUR_DOCTYPE, name)
		doc.check_permission("write")

	doc.set(COLOUR_NAME_FIELD, colour_name)
	doc.weightage = weightage
	set_pigment_rows(doc, pigments)
	doc.save()

	return doc.name


@frappe.whitelist()
def delete_base_colour(name):
	doc = frappe.get_doc(BASE_COLOUR_DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(BASE_COLOUR_DOCTYPE, name)
