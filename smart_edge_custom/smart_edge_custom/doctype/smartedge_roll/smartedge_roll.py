import frappe
from frappe.model.document import Document


class SmartEdgeRoll(Document):
	def validate(self):
		if self.is_new() or getattr(self.flags, "smartedge_roll_internal", False):
			return
		previous = frappe.db.get_value(
			self.doctype, self.name,
			["original_meter_quantity", "current_meter_quantity", "qc_status", "process_status", "packing_status"],
			as_dict=True,
		)
		if not previous:
			return
		if self.original_meter_quantity != previous.original_meter_quantity:
			frappe.throw("Original roll quantity is immutable.")
		if self.current_meter_quantity != previous.current_meter_quantity:
			frappe.throw("Roll quantity can only be changed through a controlled roll operation.")
		for fieldname in ("qc_status", "process_status", "packing_status"):
			if self.get(fieldname) != previous.get(fieldname):
				frappe.throw("Roll status can only be changed through a controlled roll operation.")

