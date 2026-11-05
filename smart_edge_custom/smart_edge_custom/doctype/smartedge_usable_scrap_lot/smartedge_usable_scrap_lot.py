import frappe
from frappe.model.document import Document


class SmartEdgeUsableScrapLot(Document):
	def validate(self):
		if not self.is_new():
			old = self.get_doc_before_save()
			protected = ("production_job", "production_lot", "source_item", "source_batch", "scrap_item", "scrap_batch", "warehouse", "quantity", "uom", "stock_entry")
			if old and any(self.get(field) != old.get(field) for field in protected):
				frappe.throw("Posted usable scrap lot fields cannot be edited.")
