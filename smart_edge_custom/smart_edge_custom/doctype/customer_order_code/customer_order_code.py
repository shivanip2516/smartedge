import frappe
from frappe import _
from frappe.model.document import Document


class CustomerOrderCode(Document):
	def validate(self):
		self.validate_duplicate_mapping()

	def validate_duplicate_mapping(self):
		if not (self.color and self.type_finish):
			return

		existing = frappe.db.exists(
			"Customer Order Code",
			{
				"color": self.color,
				"type_finish": self.type_finish,
				"name": ["!=", self.name],
			},
		)

		if existing:
			frappe.throw(
				_("A Customer Order Code mapping already exists for this Color and Type / Finish.")
			)
