import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries


class Pigment(Document):
	def autoname(self):
		self.name = getseries("Pigment", 3)
		self.pigment_code = self.name

	def validate(self):
		self.validate_pigment_name()
		self.validate_supplier()
		self.protect_pigment_code()

	def before_insert(self):
		if not self.pigment_code:
			self.pigment_code = self.name

	def validate_pigment_name(self):
		self.pigment_name = (self.pigment_name or "").strip()
		if not self.pigment_name:
			frappe.throw(_("Pigment Name is required."))

	def validate_supplier(self):
		if self.supplier and not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier {0} does not exist.").format(frappe.bold(self.supplier)))

	def protect_pigment_code(self):
		if self.is_new():
			self.pigment_code = self.name
			return

		old_code = frappe.db.get_value(self.doctype, self.name, "pigment_code")
		if old_code and self.pigment_code != old_code:
			frappe.throw(_("Pigment Code cannot be changed."))

