"""Operator-scoped access rules for SmartEdge production planning."""

import frappe
from frappe import _


SUPERVISOR_ROLES = {"Manufacturing Manager", "System Manager"}


def is_planning_supervisor(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & SUPERVISOR_ROLES)


def get_user_employees(user=None, company=None):
	user = user or frappe.session.user
	if not user or user == "Guest":
		return set()
	filters = {"user_id": user, "status": "Active"}
	if company and frappe.get_meta("Employee", cached=True).has_field("company"):
		filters["company"] = company
	return set(frappe.get_all("Employee", filters=filters, pluck="name", limit_page_length=100))


def get_company_restrictions(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return set()
	return set(
		frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Company"},
			pluck="for_value",
			limit_page_length=500,
		)
	)


def can_access_company(company, user=None):
	if not company:
		return False
	restrictions = get_company_restrictions(user)
	return not restrictions or company in restrictions


def get_entry_company(entry):
	return frappe.db.get_value("Final EB BOM Generator", entry.get("final_eb_bom_generator"), "company")


def get_effective_operator(plan_entry, planned_employee=None):
	job = frappe.db.get_value(
		"SmartEdge Production Job",
		{"plan_entry": plan_entry, "status": ["!=", "Cancelled"]},
		["operator_employee", "status"],
		as_dict=True,
		order_by="modified desc",
	)
	return (job.get("operator_employee") if job else None) or planned_employee


def can_access_plan_entry(entry, user=None):
	user = user or frappe.session.user
	company = get_entry_company(entry)
	if not can_access_company(company, user):
		return False
	if is_planning_supervisor(user):
		return True
	assigned_employee = get_effective_operator(entry.name, entry.get("planned_employee"))
	return bool(assigned_employee and assigned_employee in get_user_employees(user, company))


def require_plan_entry_access(entry, action=None):
	if can_access_plan_entry(entry):
		return
	message = _("You are not assigned to this production operation.")
	if action:
		message = _("You are not authorized to {0} this production operation.").format(action)
	frappe.throw(message, frappe.PermissionError)


def require_planning_supervisor(action=None):
	if is_planning_supervisor():
		return
	message = _("Only an authorized production planning manager may perform this action.")
	if action:
		message = _("Only an authorized production planning manager may {0}.").format(action)
	frappe.throw(message, frappe.PermissionError)


def _sql_values(values):
	return ", ".join(frappe.db.escape(value) for value in sorted(set(values))) or "NULL"


def plan_entry_query_conditions(user=None):
	user = user or frappe.session.user
	company_restrictions = get_company_restrictions(user)
	company_condition = ""
	if company_restrictions:
		company_condition = (
			" and generator.company in ({0})".format(_sql_values(company_restrictions))
		)
	company_exists = (
		"exists (select 1 from `tabFinal EB BOM Generator` generator "
		"where generator.name = `tabSmartEdge Production Plan Entry`.final_eb_bom_generator{0})"
	).format(company_condition)
	if is_planning_supervisor(user):
		return company_exists if company_restrictions else ""
	employees = get_user_employees(user)
	if not employees:
		return "1=0"
	employee_values = _sql_values(employees)
	assignment_condition = (
		"(`tabSmartEdge Production Plan Entry`.planned_employee in ({employees}) "
		"or exists (select 1 from `tabSmartEdge Production Job` job "
		"where job.plan_entry = `tabSmartEdge Production Plan Entry`.name "
		"and job.status != 'Cancelled' and job.operator_employee in ({employees})))"
	).format(employees=employee_values)
	return f"{company_exists} and {assignment_condition}"


def production_job_query_conditions(user=None):
	user = user or frappe.session.user
	company_restrictions = get_company_restrictions(user)
	company_condition = ""
	if company_restrictions:
		company_condition = " and generator.company in ({0})".format(_sql_values(company_restrictions))
	company_exists = (
		"exists (select 1 from `tabSmartEdge Production Plan Entry` entry "
		"inner join `tabFinal EB BOM Generator` generator on generator.name = entry.final_eb_bom_generator "
		"where entry.name = `tabSmartEdge Production Job`.plan_entry{0})"
	).format(company_condition)
	if is_planning_supervisor(user):
		return company_exists if company_restrictions else ""
	employees = get_user_employees(user)
	if not employees:
		return "1=0"
	employee_values = _sql_values(employees)
	assignment_condition = (
		"(`tabSmartEdge Production Job`.operator_employee in ({employees}) "
		"or exists (select 1 from `tabSmartEdge Production Plan Entry` entry "
		"where entry.name = `tabSmartEdge Production Job`.plan_entry "
		"and entry.planned_employee in ({employees})))"
	).format(employees=employee_values)
	return f"{company_exists} and {assignment_condition}"


def plan_entry_has_permission(doc, user=None, ptype=None, debug=False, **kwargs):
	if ptype not in {"read", "write", "print", "email", "export", "share"}:
		return True
	if ptype == "read" and _is_qc_authorized_user_for_entry(doc, user):
		return True
	return can_access_plan_entry(doc, user)


def production_job_has_permission(doc, user=None, ptype=None, debug=False, **kwargs):
	if ptype not in {"read", "write", "print", "email", "export", "share"}:
		return True
	entry = frappe.get_doc("SmartEdge Production Plan Entry", doc.plan_entry)
	if ptype == "read" and _is_qc_authorized_user_for_entry(entry, user):
		return True
	return can_access_plan_entry(entry, user)


def _is_qc_authorized_user_for_entry(entry, user=None):
	"""Allow QC's existing role to read only QC checkpoint records."""
	user = user or frappe.session.user
	if user != "Administrator" and "Quality Manager" not in frappe.get_roles(user):
		return False
	if not can_access_company(get_entry_company(entry), user):
		return False
	from smart_edge_custom.manufacturing.quality_control import is_qc_entry

	return is_qc_entry(entry)
