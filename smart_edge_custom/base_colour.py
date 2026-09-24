import re

import frappe
from frappe import _

BASE_COLOUR_DOCTYPE = "Base Colour"
COLOUR_NAME_FIELD = "colour_name"


def validate_base_colour(doc, method=None):
	doc.weightage = validate_weightage(doc.get("weightage"))


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
		frappe.throw(_("Colour Name is required."))

	return colour_name.strip()


@frappe.whitelist()
def get_base_colours(search=None, start=0, page_length=50):
	if not frappe.has_permission(BASE_COLOUR_DOCTYPE, "read"):
		frappe.throw(_("Not permitted to read Base Colour"), frappe.PermissionError)

	filters = {}
	if search:
		filters[COLOUR_NAME_FIELD] = ["like", f"%{search}%"]

	return frappe.get_list(
		BASE_COLOUR_DOCTYPE,
		filters=filters,
		fields=["name", COLOUR_NAME_FIELD, "weightage"],
		order_by=f"weightage asc, {COLOUR_NAME_FIELD} asc",
		start=int(start or 0),
		page_length=int(page_length or 50),
	)


@frappe.whitelist()
def create_base_colour(colour_name, weightage):
	colour_name = validate_colour_name(colour_name)
	weightage = validate_weightage(weightage)

	doc = frappe.new_doc(BASE_COLOUR_DOCTYPE)
	doc.set(COLOUR_NAME_FIELD, colour_name)
	doc.weightage = weightage
	doc.insert()

	return doc.name


@frappe.whitelist()
def update_base_colour(name, colour_name, weightage):
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
	doc.save()

	return doc.name


@frappe.whitelist()
def delete_base_colour(name):
	doc = frappe.get_doc(BASE_COLOUR_DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(BASE_COLOUR_DOCTYPE, name)
