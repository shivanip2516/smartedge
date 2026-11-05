import frappe
from frappe.model.document import Document


class SmartEdgeUnusableScrapLot(Document):
	def validate(self):
		from smart_edge_custom.manufacturing.unusable_scrap import validate_unusable_scrap_lot

		validate_unusable_scrap_lot(self)

	def on_trash(self):
		if self.storage_stock_entry or self.disposed_quantity:
			frappe.throw("A posted unusable-scrap lot cannot be deleted.")
