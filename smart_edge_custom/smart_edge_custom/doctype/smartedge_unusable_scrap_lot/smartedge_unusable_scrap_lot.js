frappe.ui.form.on("SmartEdge Unusable Scrap Lot", {
	refresh(frm) {
		install_meter_quantity_display(frm, ["quantity", "stored_quantity", "disposed_quantity", "available_quantity"]);
		if (frm.is_new()) return;
		frm.dashboard.add_indicator(
			__("Available: {0} {1}", [format_uom_quantity(frm.doc.available_quantity || 0, frm.doc.uom), frm.doc.uom || ""]),
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
	const meter_uom = is_meter_uom(frm.doc.uom);
	const available_quantity = flt(frm.doc.available_quantity);
	const dialog = new frappe.ui.Dialog({
		title: __("Authorized Unusable Scrap Disposal"),
		fields: [
			{ fieldtype: "Float", fieldname: "quantity", label: __("Disposal Quantity ({0})", [frm.doc.uom || ""]), precision: meter_uom ? "0" : 6, reqd: 1, default: meter_uom ? Math.floor(available_quantity) : available_quantity },
			{ fieldtype: "Data", fieldname: "reason", label: __("Disposal Reason"), reqd: 1 },
			{ fieldtype: "Datetime", fieldname: "disposal_datetime", label: __("Disposal Date and Time"), reqd: 1, default: frappe.datetime.now_datetime() },
			{ fieldtype: "Link", fieldname: "employee", label: __("Responsible Employee"), options: "Employee", reqd: 1, get_query: () => ({ filters: { status: "Active", company: frm.doc.company } }) },
			{ fieldtype: "Small Text", fieldname: "remarks", label: __("Remarks") },
		],
		primary_action_label: __("Post Disposal"),
		primary_action: async (values) => {
			const quantity = meter_uom ? round_meter(values.quantity) : flt(values.quantity);
			if (quantity <= 0 || quantity > available_quantity) {
				frappe.msgprint(__("Enter a positive quantity within the available stored balance."));
				return;
			}
			dialog.set_value("quantity", quantity);
			await frappe.call({
				method: "smart_edge_custom.manufacturing.unusable_scrap.dispose_unusable_scrap",
				args: { unusable_scrap_lot: frm.doc.name, request_id, ...values, quantity },
			});
			dialog.hide();
			await frm.reload_doc();
		},
	});
	dialog.show();
}

function install_meter_quantity_display(frm, fieldnames) {
	if (!is_meter_uom(frm.doc.uom)) return;
	fieldnames.forEach((fieldname) => {
		const field = frm.get_field(fieldname);
		if (!field) return;
		field.df.precision = "0";
		field.df.formatter = (value) => format_uom_quantity(value, "Meter");
		field.refresh();
	});
}

function is_meter_uom(uom) {
	return ["meter", "metre", "m"].includes(String(uom || "").trim().toLowerCase());
}

function round_meter(value) {
	const number = Number(value);
	if (!Number.isFinite(number)) return 0;
	return number < 0 ? -Math.floor(Math.abs(number) + 0.5) : Math.floor(number + 0.5);
}

function format_uom_quantity(value, uom) {
	const number = Number(value);
	if (!Number.isFinite(number)) return "0";
	return is_meter_uom(uom)
		? number.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })
		: format_number(number, null, 6);
}
