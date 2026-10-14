import frappe
from frappe import _
from frappe.model.document import Document


class SmartEdgeProcessRoute(Document):
	def validate(self):
		if not self.route_name:
			frappe.throw(_("Route Name is required."))

		if not self.get("operations"):
			frappe.throw(_("At least one route operation is required."))

		sequences = set()
		for row in self.get("operations") or []:
			if not row.sequence:
				frappe.throw(_("Sequence is required for every route operation."))
			if row.sequence in sequences:
				frappe.throw(_("Duplicate operation sequence: {0}").format(row.sequence))
			sequences.add(row.sequence)
			if not row.operation:
				frappe.throw(_("Operation is required for every route operation."))
			if not frappe.db.exists("Operation", row.operation):
				frappe.throw(_("Operation does not exist: {0}").format(row.operation))
			if row.workstation and not frappe.db.exists("Workstation", row.workstation):
				frappe.throw(_("Workstation does not exist: {0}").format(row.workstation))

		for row in self.get("cost_heads") or []:
			if row.cost_head and not frappe.db.exists("SmartEdge Cost Head", row.cost_head):
				frappe.throw(_("Cost Head does not exist: {0}").format(row.cost_head))
