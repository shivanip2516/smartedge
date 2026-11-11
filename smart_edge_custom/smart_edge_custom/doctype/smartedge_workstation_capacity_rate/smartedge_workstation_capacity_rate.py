import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


SUPPORTED_CAPACITY_UOMS = {"Kg/Hour", "Meter/Hour"}


class SmartEdgeWorkstationCapacityRate(Document):
	def validate(self):
		self.validate_workstation()
		self.validate_rate()
		self.validate_matching_scope()
		self.validate_capacity_rows()
		self.validate_duplicate()

	def on_update(self):
		self.clear_capacity_cache()

	def after_delete(self):
		self.clear_capacity_cache()

	def clear_capacity_cache(self):
		from smart_edge_custom.manufacturing.machine_capacity import clear_capacity_request_cache

		clear_capacity_request_cache()

	def validate_workstation(self):
		disabled = frappe.db.get_value("Workstation", self.workstation, "disabled")
		if disabled is None:
			frappe.throw(_("Workstation {0} does not exist.").format(frappe.bold(self.workstation)))
		if disabled:
			frappe.throw(_("Workstation {0} is disabled.").format(frappe.bold(self.workstation)))

	def validate_rate(self):
		if flt(self.capacity_rate) <= 0:
			frappe.throw(_("Capacity Rate must be greater than zero."))
		if self.capacity_uom not in SUPPORTED_CAPACITY_UOMS:
			frappe.throw(_("Capacity Unit must be Kg/Hour or Meter/Hour."))

	def validate_matching_scope(self):
		# Capacity is configured at Workstation level.  Product-size rows are
		# validated by validate_capacity_rows(); legacy selector fields remain
		# readable for migration but no longer impose Operation/route rules.
		return

	def validate_duplicate(self):
		selector_fields = ("operation", "company", "product_size", "product_item", "process_route")
		rows = frappe.get_all(
			self.doctype,
			filters={
				"workstation": self.workstation,
				"capacity_uom": self.capacity_uom,
				"name": ["!=", self.name or ""],
			},
			fields=["name", *selector_fields],
			limit_page_length=500,
		)
		for row in rows:
			if all((row.get(fieldname) or "") == (self.get(fieldname) or "") for fieldname in selector_fields):
				frappe.throw(
					_("A capacity row with the same Workstation, Operation, product/size/route, company, and unit already exists.")
				)

	def validate_capacity_rows(self):
		seen = set()
		for row in self.get("capacity_rows") or []:
			if flt(row.capacity_rate) <= 0:
				frappe.throw(_("Capacity Rate must be greater than zero."))
			if row.capacity_uom not in SUPPORTED_CAPACITY_UOMS:
				frappe.throw(_("Capacity Unit must be Kg/Hour or Meter/Hour."))
			key = (row.product_size, row.capacity_uom)
			if key in seen:
				frappe.throw(_("Duplicate Product Size and Capacity Unit in capacity rows: {0}.").format(row.product_size))
			seen.add(key)
