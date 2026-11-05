frappe.ui.form.on("SmartEdge Recovered Material Allocation", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.status === "Reserved") {
			frm.add_custom_button(__("Cancel Allocation"), async () => {
				await frappe.call({
					method: "smart_edge_custom.manufacturing.scrap_recovery.cancel_recovered_allocation",
					args: { allocation: frm.doc.name },
				});
				await frm.reload_doc();
			});
		}
	},
});
