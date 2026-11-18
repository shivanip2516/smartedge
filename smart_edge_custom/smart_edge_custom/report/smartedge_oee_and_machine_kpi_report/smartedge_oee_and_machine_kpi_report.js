frappe.query_reports["SmartEdge OEE and Machine KPI Report"] = {
	formatter(value, row, column, data, default_formatter) {
		const meter_fields = ["planned_output", "actual_output", "first_pass_good", "rejected_quantity", "rework_quantity", "usable_scrap", "unusable_scrap"];
		if (is_meter_uom(data?.output_uom) && meter_fields.includes(column.fieldname)) {
			return format_whole_meter(value);
		}
		return default_formatter(value, row, column, data);
	},
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

function is_meter_uom(uom) {
	return ["meter", "metre", "m"].includes(String(uom || "").trim().toLowerCase());
}

function format_whole_meter(value) {
	const number = Number(value);
	if (!Number.isFinite(number)) return "0";
	const rounded = number < 0 ? -Math.floor(Math.abs(number) + 0.5) : Math.floor(number + 0.5);
	return rounded.toLocaleString();
}
