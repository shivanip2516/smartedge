import frappe
from frappe import _
from frappe.utils import cint


def lock_workstation(workstation):
	if workstation:
		frappe.db.sql(
			"select name from `tabWorkstation` where name = %s for update",
			(workstation,),
		)


def get_capacity(workstation):
	fieldname = "job_capacity" if frappe.get_meta("Workstation").has_field("job_capacity") else "production_capacity"
	return cint(frappe.db.get_value("Workstation", workstation, fieldname)) or 1


def get_reserved_work_order_count(workstation, exclude_work_order=None):
	conditions = [
		"wo.docstatus = 1",
		"wo.status not in ('Closed', 'Stopped', 'Completed', 'Cancelled')",
		"wop.workstation = %s",
	]
	values = [workstation]

	if exclude_work_order:
		conditions.append("wo.name != %s")
		values.append(exclude_work_order)

	result = frappe.db.sql(
		f"""
		select count(distinct wo.name)
		from `tabWork Order` wo
		inner join `tabWork Order Operation` wop on wop.parent = wo.name
		where {' and '.join(conditions)}
		""",
		values,
	)
	return cint(result[0][0]) if result else 0


def get_active_production_job_count(workstation, exclude_plan_entry=None):
	conditions = [
		"workstation = %s",
		"status not in ('Completed', 'Cancelled')",
	]
	values = [workstation]

	if exclude_plan_entry:
		conditions.append("plan_entry != %s")
		values.append(exclude_plan_entry)

	result = frappe.db.sql(
		f"""
		select count(distinct name)
		from `tabSmartEdge Production Job`
		where {' and '.join(conditions)}
		""",
		values,
	)
	return cint(result[0][0]) if result else 0


def get_occupied_capacity(workstation, exclude_work_order=None, exclude_plan_entry=None):
	return get_reserved_work_order_count(workstation, exclude_work_order) + get_active_production_job_count(
		workstation, exclude_plan_entry
	)


def validate_workstation_job_capacity(workstation, work_order=None, plan_entry=None):
	if not workstation:
		return

	lock_workstation(workstation)
	maximum = get_capacity(workstation)
	occupied = get_occupied_capacity(workstation, work_order, plan_entry)
	available = maximum - occupied
	if available >= 1:
		return

	frappe.throw(
		_("""
		<p><b>Workstation Job Capacity Exceeded</b></p>
		<p>
			Workstation: {0}<br>
			Maximum Job Capacity: {1}<br>
			Currently Occupied Capacity: {2}<br>
			Available Capacity: {3}<br>
			Reference: {4}
		</p>
		<p>Cannot start or submit this operation because the selected Workstation has reached its maximum Job Capacity.</p>
		<p>Please complete the existing operation or assign another available Workstation.</p>
		""").format(
			frappe.bold(workstation),
			maximum,
			occupied,
			max(available, 0),
			frappe.bold(work_order or plan_entry or ""),
		),
		title=_("Workstation Job Capacity Exceeded"),
	)


def validate_work_order_capacity(doc):
	workstations = sorted({row.workstation for row in doc.get("operations", []) if row.workstation})
	for workstation in workstations:
		validate_workstation_job_capacity(workstation, work_order=doc.name)


class SmartEdgeJobCardCapacityMixin:
	"""Let workstation capacity, rather than employee identity, control starts."""

	def get_open_job_cards(self, employee, workstation=None):
		# ERPNext uses this method to reject an employee already working elsewhere.
		# Returning no employee-conflict rows preserves the normal workstation
		# overlap/capacity validation in get_overlap_for().
		return []

	def add_time_logs(self, **kwargs):
		args = frappe._dict(kwargs)
		if args.get("from_time") and not args.get("to_time"):
			lock_workstation(self.workstation)

		return super().add_time_logs(**kwargs)


class SmartEdgeWorkstationCapacityMixin:
	def start_job(self, job_card, from_time, employee):
		doc = frappe.get_doc("Job Card", job_card)
		doc.check_permission("write")
		lock_workstation(doc.workstation)

		return super().start_job(job_card, from_time, employee)


class SmartEdgeWorkOrderCapacityMixin:
	def before_submit(self):
		validate_work_order_capacity(self)
		return super().before_submit()
