frappe.ui.form.on("SmartEdge Usable Scrap Lot", {
	async refresh(frm) {
		install_meter_quantity_display(frm, ["quantity"]);
		if (frm.is_new() || frm.doc.status === "Cancelled") return;
		const response = await frappe.call({
			method: "smart_edge_custom.manufacturing.scrap_recovery.get_scrap_lot_context",
			args: { usable_scrap_lot: frm.doc.name },
		});
		const available = flt(response.message?.available_scrap_quantity);
		frm.dashboard.add_indicator(
			__("Available: {0} {1}", [format_uom_quantity(available, frm.doc.uom), frm.doc.uom || ""]),
			available > 0 ? "green" : "gray"
		);
		if (available > 0) {
			frm.add_custom_button(__("Start Grinder Recovery"), () => {
				frappe.new_doc("SmartEdge Scrap Recovery", { usable_scrap_lot: frm.doc.name });
			});
		}
	},
});

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

function format_uom_quantity(value, uom) {
	const number = Number(value);
	if (!Number.isFinite(number)) return "0";
	return is_meter_uom(uom)
		? number.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })
		: format_number(number, null, 6);
}
