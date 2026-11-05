from frappe.model.document import Document


class SmartEdgeRecoveryReuseRule(Document):
	def validate(self):
		from smart_edge_custom.manufacturing.scrap_recovery import validate_reuse_rule

		validate_reuse_rule(self)
