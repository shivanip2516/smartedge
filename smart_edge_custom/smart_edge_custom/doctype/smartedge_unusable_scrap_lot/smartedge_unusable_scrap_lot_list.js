frappe.listview_settings["SmartEdge Unusable Scrap Lot"] = {
	add_fields: ["status", "available_quantity", "uom"],
	get_indicator(doc) {
		const colors = {
			Recorded: "gray",
			"Pending Storage": "orange",
			Stored: "blue",
			"Pending Disposal": "orange",
			Disposed: "green",
			Cancelled: "red",
		};
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
