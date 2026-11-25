// The visible list status is the manufacturing lifecycle, not material readiness.
frappe.listview_settings["Final EB BOM Generator"] = {
	add_fields: ["production_status", "status"],
	formatters: {
		status(value, field, doc) {
			return frappe.utils.escape_html(doc.status || value || "Material Planning Pending");
		},
	},
	get_indicator(doc) {
		const status = doc.status || "Material Planning Pending";
		const colors = {
			Completed: "green",
			"Material Planning Pending": "grey",
			"Material Planning Completed": "blue",
			"Material Production Pending": "orange",
			"Material Ready": "green",
			"Production Planning Pending": "blue",
			"In Progress": "orange",
			"Partially Planned": "orange",
			"Fully Planned": "blue",
			Planned: "blue",
			"Ready for Planning": "blue",
			Cancelled: "red",
			"On Hold": "orange",
		};
		return [__(status), colors[status] || "grey", `status,=,${status}`];
	},
};
