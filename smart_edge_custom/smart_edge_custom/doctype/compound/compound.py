from frappe.model.document import Document

from smart_edge_custom.masters import validate_compound_doc


class Compound(Document):
	def validate(self):
		validate_compound_doc(self)
