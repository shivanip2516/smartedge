from frappe.model.document import Document

from smart_edge_custom.masters import validate_additive_doc


class Additive(Document):
	def validate(self):
		validate_additive_doc(self)
