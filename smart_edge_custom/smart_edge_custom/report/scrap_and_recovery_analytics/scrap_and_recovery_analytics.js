frappe.query_reports["Scrap and Recovery Analytics"] = {
	formatter(value, row, column, data, default_formatter) {
		if (["recovery_percent", "unusable_scrap_percent", "recovery_utilization_percent"].includes(column.fieldname)
			&& (value === null || value === undefined || value === "")) {
			return __("N/A");
		}
		return default_formatter(value, row, column, data);
	},
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start() },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", reqd: 1, default: frappe.defaults.get_user_default("Company") },
		{ fieldname: "workstation", label: __("Workstation"), fieldtype: "Link", options: "Workstation" },
		{ fieldname: "finished_item", label: __("Finished Item / Product"), fieldtype: "Link", options: "Item" },
		{ fieldname: "shift", label: __("Shift"), fieldtype: "Link", options: "Shift Type" },
		{ fieldname: "operation", label: __("Operation"), fieldtype: "Link", options: "Operation" },
		{ fieldname: "scrap_type", label: __("Scrap Type"), fieldtype: "Select", options: "\nUsable Scrap\nUnusable Scrap" },
		{ fieldname: "batch_or_lot", label: __("Batch / Production Lot"), fieldtype: "Data" },
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Workstation\nProduct\nShift\nOperation\nDate\nProduction Job",
			default: "Workstation",
			reqd: 1,
		},
	],
};
