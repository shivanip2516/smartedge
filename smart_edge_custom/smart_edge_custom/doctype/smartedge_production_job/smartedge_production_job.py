import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, getdate, now_datetime, nowdate


class SmartEdgeProductionJob(Document):
	def validate(self):
		self.set_company()
		self.map_legacy_operator()
		self.set_operator_details()
		self.validate_actual_end()
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

	def validate_actual_end(self):
		if self.status == "Completed" or not self.actual_end:
			return
		if self.is_new() or self.has_value_changed("actual_end"):
			frappe.throw(_("Actual End can only be set when completing the job."))

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
		validate_active_employee(self.operator_employee, self.company)
		if not self.shift:
			self.shift = get_current_shift(self.operator_employee)
		if not self.actual_start:
			self.actual_start = now_datetime()
		if not self.current_timer_start:
			self.current_timer_start = self.actual_start or now_datetime()
		self.status = "In Progress"
		self.save()
		self.sync_plan_entry("In Progress")
		return {"status": self.status, "operator_employee": self.operator_employee, "operator_name": self.operator_name}

	@frappe.whitelist()
	def pause_job(self, downtime_reason=None, downtime_remarks=None):
		if self.status != "In Progress":
			frappe.throw(_("Only in-progress jobs can be paused."))
		self.validate_pause_reason(downtime_reason)
		self.validate_operator_for_downtime()
		if self.get_open_downtime_rows():
			frappe.throw(_("An open downtime event already exists for this Production Job."))
		pause_time = now_datetime()
		self.accumulate_productive_time(pause_time)
		self.append(
			"downtime_events",
			{
				"start_time": pause_time,
				"downtime_reason": downtime_reason,
				"remarks": downtime_remarks,
				"employee": self.operator_employee,
			},
		)
		self.downtime_reason = downtime_reason
		self.status = "Paused"
		self.save()
		self.sync_plan_entry("On Hold")
		return {"status": self.status}

	@frappe.whitelist()
	def resume_job(self):
		if self.status != "Paused":
			frappe.throw(_("Only paused jobs can be resumed."))
		resume_time = now_datetime()
		self.close_open_downtime(resume_time)
		if not self.actual_start:
			self.actual_start = resume_time
		self.current_timer_start = resume_time
		self.status = "In Progress"
		self.save()
		self.sync_plan_entry("In Progress")
		return {"status": self.status}

	@frappe.whitelist()
	def complete_job(self, completion_type="Full Completion"):
		if self.status not in {"In Progress", "Paused"}:
			frappe.throw(_("Only started jobs can be completed."))
		if not self.operator_employee:
			frappe.throw(_("Operator is required before completing the job."))
		if not self.actual_start:
			frappe.throw(_("Actual Start is required before completing the job."))
		self.validate_completion_details(completion_type)
		completion_time = now_datetime()
		if self.status == "In Progress":
			self.accumulate_productive_time(completion_time)
		elif self.status == "Paused":
			self.close_open_downtime(completion_time)
		if not self.actual_end:
			self.actual_end = completion_time
		self.downtime_minutes = self.get_total_downtime_minutes()
		self.set_completion_remainder(completion_type)
		self.status = "Completed"
		self.save()
		self.sync_plan_entry("Completed" if completion_type == "Full Completion" else "On Hold")
		from smart_edge_custom.manufacturing.planning import release_completed_quantity_to_next_operation

		release_completed_quantity_to_next_operation(self)
		if self.get("released_to_plan_entry"):
			self.save()
		return {"status": self.status}

	def accumulate_productive_time(self, end_time=None):
		timer_start = self.current_timer_start or (self.actual_start if not flt(self.productive_elapsed_seconds) else None)
		if not timer_start:
			return
		seconds = max((get_datetime(end_time or now_datetime()) - get_datetime(timer_start)).total_seconds(), 0)
		self.productive_elapsed_seconds = flt(self.productive_elapsed_seconds) + seconds
		self.current_timer_start = None

	def validate_pause_reason(self, downtime_reason):
		if not downtime_reason:
			frappe.throw(_("Downtime Reason is required."))
		reason = frappe.get_doc("SmartEdge Downtime Reason", downtime_reason)
		reason.check_permission("read")
		if reason.disabled:
			frappe.throw(_("Select an enabled Downtime Reason."))

	def validate_operator_for_downtime(self):
		if not self.operator_employee:
			frappe.throw(_("Operator is required before pausing the job."))
		employee = frappe.get_doc("Employee", self.operator_employee)
		employee.check_permission("read")
		if employee.status != "Active":
			frappe.throw(_("Select an active Employee before pausing the job."))

	def validate_completion_details(self, completion_type):
		if completion_type not in {"Full Completion", "Partial Completion"}:
			frappe.throw(_("Select a valid Completion Type."))
		self.completion_type = completion_type
		quantity_fields = (
			"actual_qty_meter",
			"actual_qty_kg",
			"good_qty",
			"rejected_qty",
			"rework_qty",
			"usable_scrap",
			"unusable_scrap",
		)
		for fieldname in quantity_fields:
			if flt(self.get(fieldname)) < 0:
				field = self.meta.get_field(fieldname)
				frappe.throw(_("{0} cannot be negative.").format(field.label if field else fieldname))
		if not flt(self.actual_qty_meter):
			frappe.throw(_("Actual Completed Qty is required."))
		if flt(self.planned_qty_kg) and not flt(self.actual_qty_kg):
			frappe.throw(_("Actual Kg is required."))
		if completion_type == "Partial Completion" and not self.has_remaining_quantity():
			frappe.throw(_("Partial Completion requires a remaining planned quantity."))

	def has_remaining_quantity(self):
		return (flt(self.planned_qty_meter) and flt(self.actual_qty_meter) < flt(self.planned_qty_meter)) or (
			flt(self.planned_qty_kg) and flt(self.actual_qty_kg) < flt(self.planned_qty_kg)
		)

	def set_completion_remainder(self, completion_type):
		if completion_type == "Partial Completion":
			self.remaining_qty_meter = max(flt(self.planned_qty_meter) - flt(self.actual_qty_meter), 0)
			self.remaining_qty_kg = max(flt(self.planned_qty_kg) - flt(self.actual_qty_kg), 0)
			return
		self.remaining_qty_meter = 0
		self.remaining_qty_kg = 0

	def get_open_downtime(self):
		rows = self.get_open_downtime_rows()
		return rows[0] if len(rows) == 1 else None

	def get_open_downtime_rows(self):
		return [row for row in self.get("downtime_events") or [] if row.start_time and not row.end_time]

	def close_open_downtime(self, end_time):
		rows = self.get_open_downtime_rows()
		if not rows:
			frappe.throw(_("No open downtime event found for this Production Job."))
		if len(rows) > 1:
			frappe.throw(_("Multiple open downtime events found for this Production Job."))
		row = rows[0]
		if not row.downtime_reason:
			frappe.throw(_("Downtime Reason is required before closing downtime."))
		row.end_time = end_time
		duration = (get_datetime(end_time) - get_datetime(row.start_time)).total_seconds() / 60
		if duration < 0:
			frappe.throw(_("Downtime duration cannot be negative."))
		row.duration_minutes = duration

	def get_total_downtime_minutes(self):
		total = 0
		for row in self.get("downtime_events") or []:
			if row.start_time and row.end_time:
				total += max((get_datetime(row.end_time) - get_datetime(row.start_time)).total_seconds(), 0) / 60
		return total

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


def validate_active_employee(employee, company=None):
	employee_doc = frappe.get_doc("Employee", employee)
	employee_doc.check_permission("read")
	if employee_doc.status != "Active":
		frappe.throw(_("Select an active Employee as Operator before starting the job."))
	if company and frappe.get_meta("Employee", cached=True).has_field("company") and employee_doc.company != company:
		frappe.throw(_("Select an active Employee as Operator for this Company."))


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
