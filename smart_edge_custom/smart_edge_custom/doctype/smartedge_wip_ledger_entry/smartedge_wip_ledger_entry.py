import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class SmartEdgeWIPLedgerEntry(Document):
	def validate(self):
		if flt(self.quantity) <= 0:
			frappe.throw(_("WIP movement Quantity must be greater than zero."))
		if not self.is_new():
			frappe.throw(_("WIP ledger entries are append-only and cannot be edited."))

	def on_trash(self):
		frappe.throw(_("WIP ledger entries are append-only and cannot be deleted."))

