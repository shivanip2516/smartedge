import frappe
from frappe import _
from frappe.model.document import Document


class SmartEdgeBatchGenealogyMovement(Document):
	def validate(self):
		if self.direction not in {"Input", "Output", "Scrap / Recovery"}:
			frappe.throw(_("Select a valid batch movement direction."))
		if self.source_status == "Active":
			docstatus = frappe.db.get_value("Stock Entry", self.stock_entry, "docstatus")
			if docstatus != 1:
				frappe.throw(_("Active genealogy movements require a submitted Stock Entry."))
		batch_item = frappe.db.get_value("Batch", self.batch_no, "item")
		if batch_item != self.item_code:
			frappe.throw(
				_("Batch {0} does not belong to Item {1}.").format(self.batch_no, self.item_code)
			)
		if self.warehouse:
			warehouse_company = frappe.db.get_value("Warehouse", self.warehouse, "company")
			if warehouse_company and warehouse_company != self.company:
				frappe.throw(_("The batch movement Warehouse must belong to the Stock Entry Company."))
