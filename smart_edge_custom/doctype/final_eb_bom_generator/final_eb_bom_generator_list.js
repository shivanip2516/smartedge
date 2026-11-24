// The visible list status is the manufacturing lifecycle, not material readiness.
frappe.listview_settings["Final EB BOM Generator"] = {
	add_fields: ["production_status", "status"],
	formatters: {
		status(value, field, doc) {
			return frappe.utils.escape_html(doc.production_status || value || "Draft");
		},
	},
	get_indicator(doc) {
		const status = doc.production_status || doc.status || "Draft";
		const colors = {
			Completed: "green",
			"In Progress": "orange",
			"Partially Planned": "orange",
			Planned: "blue",
			"Ready for Planning": "blue",
			Cancelled: "red",
			"On Hold": "orange",
		};
		return [__(status), colors[status] || "grey", `production_status,=,${status}`];
	},
};
