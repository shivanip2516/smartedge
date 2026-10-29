import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt


class SmartEdgeProductionLot(Document):
	def autoname(self):
		prefix = (self.naming_prefix or "LOT").strip().upper()
		if not re.fullmatch(r"[A-Z0-9]{2,8}", prefix):
			frappe.throw(_("Production Lot Prefix must contain 2 to 8 letters or numbers."))
		self.naming_prefix = prefix
		self.name = make_autoname(f"{prefix}-.YYYY.-.#####")

	def validate(self):
		if not self.production_job or not self.final_eb_bom_generator or not self.finished_item:
			frappe.throw(_("Production Job, Final EB BOM Generator, and Finished Item are required."))
		if flt(self.produced_quantity) <= 0:
			frappe.throw(_("Produced Quantity must be greater than zero."))

