import frappe
from frappe import _
from frappe.model.document import Document


class SmartEdgeRecoveredMaterialAllocation(Document):
	def validate(self):
		from smart_edge_custom.manufacturing.scrap_recovery import validate_recovered_allocation

		validate_recovered_allocation(self)

	def on_trash(self):
		if self.status == "Consumed" or self.consumption_stock_entry:
			frappe.throw(_("Consumed recovered-material allocations cannot be deleted."))
