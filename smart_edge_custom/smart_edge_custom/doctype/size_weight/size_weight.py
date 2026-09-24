from frappe.model.document import Document

from smart_edge_custom.masters import validate_size_weight_doc


class SizeWeight(Document):
	def validate(self):
		validate_size_weight_doc(self)
