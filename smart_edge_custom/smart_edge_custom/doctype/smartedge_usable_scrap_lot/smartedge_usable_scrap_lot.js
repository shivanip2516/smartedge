frappe.ui.form.on("SmartEdge Usable Scrap Lot", {
	async refresh(frm) {
		if (frm.is_new() || frm.doc.status === "Cancelled") return;
		const response = await frappe.call({
			method: "smart_edge_custom.manufacturing.scrap_recovery.get_scrap_lot_context",
			args: { usable_scrap_lot: frm.doc.name },
		});
		const available = flt(response.message?.available_scrap_quantity);
		frm.dashboard.add_indicator(
			__("Available: {0} {1}", [format_number(available, null, 6), frm.doc.uom || ""]),
			available > 0 ? "green" : "gray"
		);
		if (available > 0) {
			frm.add_custom_button(__("Start Grinder Recovery"), () => {
				frappe.new_doc("SmartEdge Scrap Recovery", { usable_scrap_lot: frm.doc.name });
			});
		}
	},
});
