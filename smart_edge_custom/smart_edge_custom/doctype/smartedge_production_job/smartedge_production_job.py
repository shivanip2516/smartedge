import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate, now_datetime, nowdate


class SmartEdgeProductionJob(Document):
	def validate(self):
		self.set_company()
		self.map_legacy_operator()
		self.set_operator_details()
		self.set_durations()

	def set_company(self):
		if self.company or not self.final_eb_bom_generator:
			return
		self.company = frappe.db.get_value("Final EB BOM Generator", self.final_eb_bom_generator, "company")

	def map_legacy_operator(self):
		if self.operator_employee or not self.operator:
			return
		employee = get_employee_for_user(self.operator, self.company)
		if employee:
			self.operator_employee = employee

	def set_operator_details(self):
		if self.operator_employee:
			employee = frappe.db.get_value(
				"Employee",
				self.operator_employee,
				["employee_name", "designation", "department"],
				as_dict=True,
			)
			if employee:
				self.operator_name = employee.employee_name
				self.operator_designation = employee.designation
				self.operator_department = employee.department
		if self.supervisor_employee:
			self.supervisor_name = frappe.db.get_value("Employee", self.supervisor_employee, "employee_name")

	def set_durations(self):
		self.planned_duration_hours = get_duration_hours(self.planned_start, self.planned_end)
		self.actual_duration_hours = get_duration_hours(self.actual_start, self.actual_end)

	@frappe.whitelist()
	def start_job(self):
		if self.status not in {"Draft", "On Hold", "Paused"}:
			frappe.throw(_("Only Draft, Paused, or On Hold jobs can be started."))
		if not self.operator_employee:
			self.operator_employee = get_current_employee(self.company)
		if not self.operator_employee:
			frappe.throw(_("Select an active Employee as Operator before starting the job."))
		if not self.shift:
			self.shift = get_current_shift(self.operator_employee)
		if not self.actual_start:
			self.actual_start = now_datetime()
		self.status = "In Progress"
		self.save()
		self.sync_plan_entry("In Progress")
		return {"status": self.status, "operator_employee": self.operator_employee, "operator_name": self.operator_name}

	@frappe.whitelist()
	def pause_job(self):
		if self.status != "In Progress":
			frappe.throw(_("Only in-progress jobs can be paused."))
		self.status = "Paused"
		self.save()
		self.sync_plan_entry("On Hold")
		return {"status": self.status}

	@frappe.whitelist()
	def resume_job(self):
		if self.status != "Paused":
			frappe.throw(_("Only paused jobs can be resumed."))
		if not self.actual_start:
			self.actual_start = now_datetime()
		self.status = "In Progress"
		self.save()
		self.sync_plan_entry("In Progress")
		return {"status": self.status}

	@frappe.whitelist()
	def complete_job(self):
		if self.status not in {"In Progress", "Paused"}:
			frappe.throw(_("Only started jobs can be completed."))
		if not self.operator_employee:
			frappe.throw(_("Operator is required before completing the job."))
		if not self.actual_start:
			frappe.throw(_("Actual Start is required before completing the job."))
		if not (flt(self.actual_qty_meter) or flt(self.actual_qty_kg) or flt(self.good_qty)):
			frappe.throw(_("Enter actual production quantity before completing the job."))
		if not self.actual_end:
			self.actual_end = now_datetime()
		self.status = "Completed"
		self.save()
		self.sync_plan_entry("Completed")
		return {"status": self.status}

	def sync_plan_entry(self, status):
		if not self.plan_entry:
			return
		updates = {"status": status}
		if frappe.get_meta("SmartEdge Production Plan Entry", cached=True).has_field("production_job"):
			updates["production_job"] = self.name
		frappe.db.set_value("SmartEdge Production Plan Entry", self.plan_entry, updates, update_modified=True)
		if self.final_eb_bom_generator:
			from smart_edge_custom.manufacturing.planning import update_generator_planning_status

			update_generator_planning_status(self.final_eb_bom_generator)


def get_duration_hours(start, end):
	if not start or not end:
		return 0
	return (get_datetime(end) - get_datetime(start)).total_seconds() / 3600


def get_employee_for_user(user, company=None):
	if not user:
		return None
	filters = {"user_id": user, "status": "Active"}
	if company and frappe.get_meta("Employee", cached=True).has_field("company"):
		filters["company"] = company
	employees = frappe.get_all("Employee", filters=filters, pluck="name", limit=2)
	if len(employees) == 1:
		return employees[0]
	if company:
		employees = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, pluck="name", limit=2)
		if len(employees) == 1:
			return employees[0]
	return None


def get_current_employee(company=None):
	return get_employee_for_user(frappe.session.user, company)


def get_current_shift(employee):
	if not employee or not frappe.db.exists("DocType", "Shift Assignment"):
		return None
	today = getdate(nowdate())
	rows = frappe.get_all(
		"Shift Assignment",
		filters={"employee": employee, "docstatus": ["<", 2]},
		fields=["shift_type", "start_date", "end_date", "status"],
		order_by="start_date desc",
		limit_page_length=20,
	)
	for row in rows:
		if row.get("status") and row.status not in {"Active", "Approved"}:
			continue
		if row.start_date and getdate(row.start_date) > today:
			continue
		if row.end_date and getdate(row.end_date) < today:
			continue
		return row.shift_type
	return None
