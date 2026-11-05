import frappe
from frappe.model.document import Document


class SmartEdgeScrapRecovery(Document):
	def validate(self):
		from smart_edge_custom.manufacturing.scrap_recovery import validate_recovery_document

		validate_recovery_document(self)

	def on_trash(self):
		if self.grinder_stock_entry or self.pulverizer_stock_entry:
			frappe.throw("A recovery record with stock postings cannot be deleted; cancel the Stock Entries first.")
