frappe.ui.form.on("SmartEdge Unusable Scrap Lot", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.dashboard.add_indicator(
			__("Available: {0} {1}", [format_number(frm.doc.available_quantity || 0, null, 6), frm.doc.uom || ""]),
			flt(frm.doc.available_quantity) > 0 ? "orange" : "gray"
		);
		if (frm.doc.status === "Stored") {
			frm.add_custom_button(__("Request Disposal"), async () => {
				await frappe.call({
					method: "smart_edge_custom.manufacturing.unusable_scrap.request_disposal",
					args: { unusable_scrap_lot: frm.doc.name },
				});
				await frm.reload_doc();
			});
		}
		if (frm.doc.status === "Pending Disposal" && flt(frm.doc.available_quantity) > 0) {
			frm.add_custom_button(__("Dispose Scrap"), () => open_disposal_dialog(frm));
		}
	},
});

function open_disposal_dialog(frm) {
	const request_id = frappe.utils.get_random(16);
	const dialog = new frappe.ui.Dialog({
		title: __("Authorized Unusable Scrap Disposal"),
		fields: [
			{ fieldtype: "Float", fieldname: "quantity", label: __("Disposal Quantity ({0})", [frm.doc.uom || ""]), precision: 6, reqd: 1, default: frm.doc.available_quantity },
			{ fieldtype: "Data", fieldname: "reason", label: __("Disposal Reason"), reqd: 1 },
			{ fieldtype: "Datetime", fieldname: "disposal_datetime", label: __("Disposal Date and Time"), reqd: 1, default: frappe.datetime.now_datetime() },
			{ fieldtype: "Link", fieldname: "employee", label: __("Responsible Employee"), options: "Employee", reqd: 1, get_query: () => ({ filters: { status: "Active", company: frm.doc.company } }) },
			{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") },
		],
		primary_action_label: __("Post Disposal"),
		primary_action: async (values) => {
			if (flt(values.quantity) <= 0 || flt(values.quantity) > flt(frm.doc.available_quantity)) {
				frappe.msgprint(__("Enter a positive quantity within the available stored balance."));
				return;
			}
			await frappe.call({
				method: "smart_edge_custom.manufacturing.unusable_scrap.dispose_unusable_scrap",
				args: { unusable_scrap_lot: frm.doc.name, request_id, ...values },
			});
			dialog.hide();
			await frm.reload_doc();
		},
	});
	dialog.show();
}
