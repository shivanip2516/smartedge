import re

import frappe
from frappe import _

PVC_EDGE_BAND_NAME_PATTERN = re.compile(r"^.+ EB .+ .+ \(.+\)$")
PVC_EDGE_BAND_NAME_FIELDS = (
	"custom_size",
	"custom_color",
	"custom_type_short_code",
	"product_code",
)
OPTIONAL_DYNAMIC_MANDATORY_ITEM_GROUPS = {
	"Additives",
	"Compounds",
	"Pigments",
	"Base Colour",
}
DYNAMIC_MANDATORY_ITEM_FIELDS = (
	("custom_size", "Size"),
	("custom_type_short_code", "Type Short Code"),
	("custom_color", "Color"),
	("gst_hsn_code", "HSN/SAC"),
)
WOOD_GRAIN_PRINTING_FIELDS = (
	"custom_base_shade",
	"custom_base_colour_printing",
	"custom_1st_design_print",
	"custom_2nd_design_print",
)


def validate_pvc_edge_band_item(doc, method=None):
	has_pvc_edge_band_details = _has_pvc_edge_band_details(doc)
	if has_pvc_edge_band_details:
		_sync_type_short_code_from_finish(doc)
		_sync_customer_order_code(doc)
		_sync_item_name(doc)

	_validate_dynamic_mandatory_item_fields(doc)

	if has_pvc_edge_band_details:
		_validate_wood_grain_printing_details(doc)


def _has_pvc_edge_band_details(doc):
	return any(doc.get(fieldname) for fieldname in PVC_EDGE_BAND_NAME_FIELDS)


def _validate_dynamic_mandatory_item_fields(doc):
	if doc.get("item_group") in OPTIONAL_DYNAMIC_MANDATORY_ITEM_GROUPS:
		return

	missing_labels = [
		label
		for fieldname, label in DYNAMIC_MANDATORY_ITEM_FIELDS
		if doc.meta.has_field(fieldname) and not doc.get(fieldname)
	]

	if missing_labels:
		frappe.throw(
			_("The following fields are mandatory for this Item Group: {0}").format(
				", ".join(missing_labels)
			)
		)


def _sync_item_name(doc):
	if not all(doc.get(fieldname) for fieldname in PVC_EDGE_BAND_NAME_FIELDS):
		return

	generated_item_name = "{0} EB {1} {2} ({3})".format(
		doc.custom_size,
		doc.custom_color,
		doc.custom_type_short_code,
		doc.product_code,
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


def _sync_customer_order_code(doc):
	if not (doc.get("custom_color") and doc.get("custom_type_finish")):
		doc.set("product_code", None)
		return

	doc.set("product_code", get_customer_order_code(doc.custom_color, doc.custom_type_finish))


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


@frappe.whitelist()
def get_customer_order_code(color, type_finish):
	if not (color and type_finish):
		return None

	return frappe.db.get_value(
		"Customer Order Code",
		{
			"color": color,
			"type_finish": type_finish,
		},
		"order_code",
	)
