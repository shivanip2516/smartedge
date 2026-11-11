frappe.query_reports["SmartEdge OEE and Machine KPI Report"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "shift", label: __("Shift"), fieldtype: "Link", options: "Shift Type" },
		{ fieldname: "workstation", label: __("Workstation"), fieldtype: "Link", options: "Workstation" },
		{ fieldname: "operation", label: __("Operation"), fieldtype: "Link", options: "Operation" },
		{ fieldname: "product", label: __("Product"), fieldtype: "Link", options: "Item" },
		{ fieldname: "production_job", label: __("Production Job"), fieldtype: "Link", options: "SmartEdge Production Job" },
	],
};
