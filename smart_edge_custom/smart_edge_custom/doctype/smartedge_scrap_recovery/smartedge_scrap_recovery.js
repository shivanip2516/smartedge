frappe.ui.form.on("SmartEdge Scrap Recovery", {
	setup(frm) {
		frm.set_query("usable_scrap_lot", () => ({ filters: { status: "Active" } }));
		const employee_query = () => ({ filters: { status: "Active", ...(frm.doc.company ? { company: frm.doc.company } : {}) } });
		frm.set_query("grinder_employee", employee_query);
		frm.set_query("pulverizer_employee", employee_query);
	},

	async usable_scrap_lot(frm) {
		if (!frm.doc.usable_scrap_lot) return;
		const response = await frappe.call({
			method: "smart_edge_custom.manufacturing.scrap_recovery.get_scrap_lot_context",
			args: { usable_scrap_lot: frm.doc.usable_scrap_lot },
		});
		const values = response.message || {};
		for (const fieldname of Object.keys(values)) await frm.set_value(fieldname, values[fieldname]);
	},

	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Complete Grinder Processing"), async () => {
				await frm.save();
				await frappe.call({
					method: "smart_edge_custom.manufacturing.scrap_recovery.complete_grinder",
					args: { recovery: frm.doc.name },
					freeze: true,
					freeze_message: __("Posting Grinder stock movement…"),
				});
				await frm.reload_doc();
			}, __("Recovery"));
		}
		if (frm.doc.status === "Ground") {
			frm.add_custom_button(__("Complete Pulverizer Processing"), async () => {
				await frm.save();
				await frappe.call({
					method: "smart_edge_custom.manufacturing.scrap_recovery.complete_pulverizer",
					args: { recovery: frm.doc.name },
					freeze: true,
					freeze_message: __("Posting Pulverizer stock movement…"),
				});
				await frm.reload_doc();
			}, __("Recovery"));
		}
	},
});
