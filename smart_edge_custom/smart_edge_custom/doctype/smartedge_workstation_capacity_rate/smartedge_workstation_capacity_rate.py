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
		if not self.operation and any((self.product_size, self.product_item, self.process_route)):
			frappe.throw(_("Operation is required for product, size, or route-specific capacity."))
		if self.process_route and not (self.product_size or self.product_item):
			frappe.throw(_("A route-specific capacity must also specify Product Size or Finished Item."))

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
