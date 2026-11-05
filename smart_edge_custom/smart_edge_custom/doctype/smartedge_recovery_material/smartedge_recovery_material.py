from frappe.model.document import Document


class SmartEdgeRecoveryMaterial(Document):
	def validate(self):
		from smart_edge_custom.manufacturing.scrap_recovery import validate_recovery_material

		validate_recovery_material(self)
