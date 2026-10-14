import frappe


def execute():
	if not frappe.db.exists("DocType", "SmartEdge Production Job"):
		return
	meta = frappe.get_meta("SmartEdge Production Job")
	if not meta.has_field("operator") or not meta.has_field("operator_employee"):
		return

	jobs = frappe.get_all(
		"SmartEdge Production Job",
		filters={"operator": ["is", "set"], "operator_employee": ["is", "not set"]},
		fields=["name", "operator", "final_eb_bom_generator"],
		limit_page_length=0,
	)
	unmatched = []
	for job in jobs:
		company = None
		if job.final_eb_bom_generator:
			company = frappe.db.get_value("Final EB BOM Generator", job.final_eb_bom_generator, "company")
		employee = find_employee(job.operator, company)
		if not employee:
			unmatched.append(f"{job.name}: {job.operator}")
			continue
		values = {"operator_employee": employee.name}
		if meta.has_field("operator_name"):
			values["operator_name"] = employee.employee_name
		if meta.has_field("operator_designation"):
			values["operator_designation"] = employee.designation
		if meta.has_field("operator_department"):
			values["operator_department"] = employee.department
		frappe.db.set_value("SmartEdge Production Job", job.name, values, update_modified=False)

	if unmatched:
		print("SmartEdge Production Job operator migration unmatched values:")
		for value in unmatched:
			print(f" - {value}")


def find_employee(user, company=None):
	filters = {"user_id": user, "status": "Active"}
	if company and frappe.get_meta("Employee", cached=True).has_field("company"):
		filters["company"] = company
	employees = frappe.get_all(
		"Employee",
		filters=filters,
		fields=["name", "employee_name", "designation", "department"],
		limit=2,
	)
	if len(employees) == 1:
		return employees[0]
	if company:
		employees = frappe.get_all(
			"Employee",
			filters={"user_id": user, "status": "Active"},
			fields=["name", "employee_name", "designation", "department"],
			limit=2,
		)
		if len(employees) == 1:
			return employees[0]
	return None
