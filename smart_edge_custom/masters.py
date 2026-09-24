import json
import re

import frappe
from frappe import _
from frappe.utils import flt

ADDITIVE_DOCTYPE = "Additive"
COMPOUND_DOCTYPE = "Compound"
SIZE_WEIGHT_DOCTYPE = "Size Weight"


def validate_text(value, label):
	value = (value or "").strip()
	if not value:
		frappe.throw(_("{0} is required.").format(_(label)))
	return value


def validate_number(value, label, allow_zero=False):
	if value in (None, "") or isinstance(value, bool):
		frappe.throw(_("{0} must be a valid number.").format(_(label)))

	if isinstance(value, str) and not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", value.strip()):
		frappe.throw(_("{0} must be a valid number.").format(_(label)))

	number = flt(value)
	if number < 0 or (number == 0 and not allow_zero):
		frappe.throw(_("{0} must be greater than zero.").format(_(label)))

	return number


def parse_rows(rows):
	if isinstance(rows, str):
		rows = json.loads(rows or "[]")
	return rows or []


def validate_additive_doc(doc):
	doc.additive_name = validate_text(doc.additive_name, "Additive Name")
	if doc.remark:
		doc.remark = doc.remark.strip()


def validate_compound_doc(doc):
	doc.compound_name = validate_text(doc.compound_name, "Compound Name")
	if doc.note:
		doc.note = doc.note.strip()

	if not doc.get("additives"):
		frappe.throw(_("At least one additive row is required."))

	total = 0
	for row in doc.additives:
		if not row.additive:
			frappe.throw(_("Additive is required in every recipe row."))

		if not frappe.db.exists(ADDITIVE_DOCTYPE, row.additive):
			frappe.throw(_("Additive {0} does not exist.").format(frappe.bold(row.additive)))

		row.quantity_kg = validate_number(row.quantity_kg, "Quantity")
		total += row.quantity_kg

	doc.total_kg = total


def validate_size_weight_doc(doc):
	doc.size_name = validate_text(doc.size_name, "Size Name")
	doc.width = validate_number(doc.width, "Width")
	doc.thickness = validate_number(doc.thickness, "Thickness")
	doc.factor = validate_number(doc.factor, "Factor")
	if doc.note:
		doc.note = doc.note.strip()
	doc.weight_per_meter_g = doc.width * doc.thickness * doc.factor


def rename_if_needed(doctype, doc, fieldname, value):
	if doc.meta.autoname == f"field:{fieldname}" and doc.name != value:
		new_name = frappe.rename_doc(doctype, doc.name, value)
		doc = frappe.get_doc(doctype, new_name)
		doc.check_permission("write")
	return doc


def set_compound_rows(doc, rows):
	doc.set("additives", [])
	for row in parse_rows(rows):
		doc.append(
			"additives",
			{
				"additive": (row.get("additive") or "").strip(),
				"quantity_kg": row.get("quantity_kg"),
			},
		)


def additive_search_filters(search):
	if not search:
		return {}
	return [
		["Additive", "additive_name", "like", f"%{search}%"],
		["Additive", "remark", "like", f"%{search}%"],
	]


@frappe.whitelist()
def get_additives(search=None, start=0, page_length=100):
	if not frappe.has_permission(ADDITIVE_DOCTYPE, "read"):
		frappe.throw(_("Not permitted to read Additive"), frappe.PermissionError)

	or_filters = additive_search_filters(search)
	return frappe.get_list(
		ADDITIVE_DOCTYPE,
		filters={},
		or_filters=or_filters,
		fields=["name", "additive_name", "remark"],
		order_by="additive_name asc",
		start=int(start or 0),
		page_length=int(page_length or 100),
	)


@frappe.whitelist()
def create_additive(additive_name, remark=None):
	doc = frappe.new_doc(ADDITIVE_DOCTYPE)
	doc.additive_name = additive_name
	doc.remark = remark
	doc.insert()
	return doc.name


@frappe.whitelist()
def update_additive(name, additive_name, remark=None):
	doc = frappe.get_doc(ADDITIVE_DOCTYPE, name)
	doc.check_permission("write")
	doc = rename_if_needed(
		ADDITIVE_DOCTYPE, doc, "additive_name", validate_text(additive_name, "Additive Name")
	)
	doc.additive_name = additive_name
	doc.remark = remark
	doc.save()
	return doc.name


@frappe.whitelist()
def delete_additive(name):
	doc = frappe.get_doc(ADDITIVE_DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(ADDITIVE_DOCTYPE, name)


@frappe.whitelist()
def get_compounds(search=None, start=0, page_length=100):
	if not frappe.has_permission(COMPOUND_DOCTYPE, "read"):
		frappe.throw(_("Not permitted to read Compound"), frappe.PermissionError)

	filters = []
	if search:
		like = f"%{search}%"
		names = frappe.get_all(
			"Compound Additive",
			filters={"additive": ["like", like]},
			pluck="parent",
			distinct=True,
		)
		filters = [["compound_name", "like", like], ["note", "like", like], ["name", "in", names or [""]]]

	rows = frappe.get_list(
		COMPOUND_DOCTYPE,
		or_filters=filters,
		fields=["name", "compound_name", "note", "total_kg"],
		order_by="compound_name asc",
		start=int(start or 0),
		page_length=int(page_length or 100),
	)

	for row in rows:
		row.additives = frappe.get_all(
			"Compound Additive",
			filters={"parent": row.name, "parenttype": COMPOUND_DOCTYPE},
			fields=["additive", "quantity_kg"],
			order_by="idx asc",
		)

	return rows


@frappe.whitelist()
def create_compound(compound_name, note=None, additives=None):
	doc = frappe.new_doc(COMPOUND_DOCTYPE)
	doc.compound_name = compound_name
	doc.note = note
	set_compound_rows(doc, additives)
	doc.insert()
	return doc.name


@frappe.whitelist()
def update_compound(name, compound_name, note=None, additives=None):
	doc = frappe.get_doc(COMPOUND_DOCTYPE, name)
	doc.check_permission("write")
	doc = rename_if_needed(
		COMPOUND_DOCTYPE, doc, "compound_name", validate_text(compound_name, "Compound Name")
	)
	doc.compound_name = compound_name
	doc.note = note
	set_compound_rows(doc, additives)
	doc.save()
	return doc.name


@frappe.whitelist()
def delete_compound(name):
	doc = frappe.get_doc(COMPOUND_DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(COMPOUND_DOCTYPE, name)


@frappe.whitelist()
def get_size_weights(search=None, start=0, page_length=100):
	if not frappe.has_permission(SIZE_WEIGHT_DOCTYPE, "read"):
		frappe.throw(_("Not permitted to read Size Weight"), frappe.PermissionError)

	or_filters = []
	if search:
		like = f"%{search}%"
		or_filters = [
			["Size Weight", "size_name", "like", like],
			["Size Weight", "note", "like", like],
			["Size Weight", "width", "like", like],
			["Size Weight", "thickness", "like", like],
			["Size Weight", "factor", "like", like],
		]

	return frappe.get_list(
		SIZE_WEIGHT_DOCTYPE,
		or_filters=or_filters,
		fields=["name", "size_name", "width", "thickness", "factor", "weight_per_meter_g", "note"],
		order_by="size_name asc",
		start=int(start or 0),
		page_length=int(page_length or 100),
	)


@frappe.whitelist()
def create_size_weight(size_name, width, thickness, factor, note=None):
	doc = frappe.new_doc(SIZE_WEIGHT_DOCTYPE)
	doc.size_name = size_name
	doc.width = width
	doc.thickness = thickness
	doc.factor = factor
	doc.note = note
	doc.insert()
	return doc.name


@frappe.whitelist()
def update_size_weight(name, size_name, width, thickness, factor, note=None):
	doc = frappe.get_doc(SIZE_WEIGHT_DOCTYPE, name)
	doc.check_permission("write")
	doc = rename_if_needed(SIZE_WEIGHT_DOCTYPE, doc, "size_name", validate_text(size_name, "Size Name"))
	doc.size_name = size_name
	doc.width = width
	doc.thickness = thickness
	doc.factor = factor
	doc.note = note
	doc.save()
	return doc.name


@frappe.whitelist()
def delete_size_weight(name):
	doc = frappe.get_doc(SIZE_WEIGHT_DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(SIZE_WEIGHT_DOCTYPE, name)
