async function complete_recovery_action(frm, method, freeze_message, success_message) {
	if (frm.__smartedge_recovery_action_running) return;
	frm.__smartedge_recovery_action_running = true;
	try {
		if (frm.is_dirty()) {
			await frm.save();
			if (frm.is_dirty()) return;
		}
		await frappe.call({
			method,
			args: { recovery: frm.doc.name },
			freeze: true,
			freeze_message,
		});
		await frm.reload_doc();
		frappe.show_alert({ message: success_message, indicator: "green" });
	} catch (error) {
		frappe.msgprint({
			title: __("Recovery action failed"),
			indicator: "red",
			message: error.message || error.exc || __("The recovery action could not be completed."),
		});
	} finally {
		frm.__smartedge_recovery_action_running = false;
	}
}

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
			frm.add_custom_button(
				__("Complete Grinder Processing"),
				() => complete_recovery_action(
					frm,
					"smart_edge_custom.manufacturing.scrap_recovery.complete_grinder",
					__("Posting Grinder stock movement…"),
					__("Grinding completed successfully."),
				),
				__("Recovery")
			);
		}
		if (frm.doc.status === "Ground") {
			frm.add_custom_button(
				__("Complete Pulverizer Processing"),
				() => complete_recovery_action(
					frm,
					"smart_edge_custom.manufacturing.scrap_recovery.complete_pulverizer",
					__("Posting Pulverizer stock movement…"),
					__("Pulverizer processing completed successfully."),
				),
				__("Recovery")
			);
		}
	},
});
