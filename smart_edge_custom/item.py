import re

import frappe
from frappe import _

PVC_EDGE_BAND_NAME_PATTERN = re.compile(r"^.+ EB .+ .+ \(.+\)$")
PVC_EDGE_BAND_NAME_FIELDS = (
	"custom_size",
	"custom_base_colour",
	"custom_type_short_code",
	"custom_customer_order_code",
)
WOOD_GRAIN_PRINTING_FIELDS = (
	"custom_base_shade",
	"custom_base_colour_printing",
	"custom_1st_design_print",
	"custom_2nd_design_print",
)


def validate_pvc_edge_band_item(doc, method=None):
	if not _has_pvc_edge_band_details(doc):
		return

	_sync_type_short_code_from_finish(doc)
	_sync_product_code(doc)
	_sync_item_name(doc)
	_validate_wood_grain_printing_details(doc)
	_warn_for_customer_order_code_mismatch(doc)


def _has_pvc_edge_band_details(doc):
	return any(doc.get(fieldname) for fieldname in PVC_EDGE_BAND_NAME_FIELDS)


def _sync_item_name(doc):
	if not all(doc.get(fieldname) for fieldname in PVC_EDGE_BAND_NAME_FIELDS):
		return

	generated_item_name = "{0} EB {1} {2} ({3})".format(
		doc.custom_size,
		doc.custom_base_colour,
		doc.custom_type_short_code,
		doc.custom_customer_order_code,
	)

	if _should_update_item_name(doc):
		doc.item_name = generated_item_name


def _should_update_item_name(doc):
	if not doc.get("item_name"):
		return True

	if doc.is_new():
		return doc.item_name == doc.get("item_code") or _looks_like_pvc_edge_band_name(doc.item_name)

	previous_doc = doc.get_doc_before_save()
	if not previous_doc:
		return False

	source_changed = any(doc.has_value_changed(fieldname) for fieldname in PVC_EDGE_BAND_NAME_FIELDS)
	item_name_changed = doc.has_value_changed("item_name")

	return source_changed and (
		not item_name_changed
		or doc.item_name == previous_doc.item_name
		or _looks_like_pvc_edge_band_name(previous_doc.item_name)
	)


def _looks_like_pvc_edge_band_name(item_name):
	return bool(item_name and PVC_EDGE_BAND_NAME_PATTERN.match(item_name))


def _sync_type_short_code_from_finish(doc):
	if not doc.get("custom_type_finish"):
		doc.set("custom_type_short_code", None)
		return

	abbreviation = frappe.db.get_value(
		"PVC Type Finish",
		doc.custom_type_finish,
		"abbreviation",
	)
	doc.set("custom_type_short_code", abbreviation)


def _sync_product_code(doc):
	if not doc.get("custom_base_colour"):
		doc.set("product_code", None)
		return

	company_abbreviation = _get_company_abbreviation(doc)

	if not company_abbreviation:
		doc.set("product_code", None)
		return

	doc.set("product_code", "{0} - {1}".format(company_abbreviation, doc.custom_base_colour))


def _get_company_abbreviation(doc):
	company = (
		doc.get("company")
		or frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company")
	)

	if not company:
		return None

	return frappe.db.get_value("Company", company, "abbr")


def _validate_wood_grain_printing_details(doc):
	if doc.get("custom_product_type_design_category") != "Wood Grain":
		for fieldname in WOOD_GRAIN_PRINTING_FIELDS:
			doc.set(fieldname, None)

		return

	missing_labels = [
		doc.meta.get_label(fieldname) or fieldname
		for fieldname in WOOD_GRAIN_PRINTING_FIELDS
		if not doc.get(fieldname)
	]

	if missing_labels:
		frappe.throw(
			_("The following fields are required for Wood Grain items: {0}").format(", ".join(missing_labels))
		)


def _warn_for_customer_order_code_mismatch(doc):
	if not (doc.get("custom_base_colour") and doc.get("custom_customer_order_code")):
		return

	matching_item = _get_item_with_different_customer_order_code(
		doc,
		"custom_type_finish",
	) or _get_item_with_different_customer_order_code(doc, "custom_type_short_code")

	if not matching_item:
		return

	frappe.msgprint(
		_(
			"Customer Order Code {0} differs from existing Item {1}, which uses {2} "
			"for the same Base Colour + Type combination. Please confirm this is intentional "
			"or correct the code."
		).format(
			frappe.bold(doc.custom_customer_order_code),
			frappe.bold(matching_item.name),
			frappe.bold(matching_item.custom_customer_order_code),
		),
		title=_("Customer Order Code Mismatch"),
		indicator="orange",
	)


def _get_item_with_different_customer_order_code(doc, type_fieldname):
	if not doc.get(type_fieldname):
		return None

	filters = {
		"custom_base_colour": doc.custom_base_colour,
		type_fieldname: doc.get(type_fieldname),
		"custom_customer_order_code": ["!=", doc.custom_customer_order_code],
	}

	if not doc.is_new():
		filters["name"] = ["!=", doc.name]

	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["name", "custom_customer_order_code"],
		limit=1,
	)

	return items[0] if items else None
