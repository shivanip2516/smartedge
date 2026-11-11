frappe.ui.form.on("SmartEdge Production Lot", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Print Label"), async () => {
			const result = await frappe.call({
				method: "smart_edge_custom.manufacturing.production_lot.get_print_label_url",
				args: { production_lot: frm.doc.name },
			});
			if (result.message?.url) window.open(result.message.url, "_blank");
		});
	},
});
