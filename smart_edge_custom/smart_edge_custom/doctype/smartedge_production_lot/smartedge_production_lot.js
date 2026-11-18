frappe.ui.form.on("SmartEdge Production Lot", {
	async refresh(frm) {
		install_lot_quantity_formatters(frm);
		const wrapper = frm.fields_dict.traceability_details?.$wrapper;
		if (!wrapper || frm.is_new()) return;
		wrapper.html(`<div class="text-muted">${__("Loading recorded input batches...")}</div>`);
		try {
			const { message } = await frappe.call({
				method: "smart_edge_custom.manufacturing.production_lot.get_production_lot_genealogy",
				args: { reference: frm.doc.name, reference_type: "Production Lot", direction: "Backward" },
			});
			const rows = (message?.input_batches || []).map((row) => `
				<tr><td>${frappe.utils.escape_html(row.item_name || row.item_code)}</td>
				<td><a href="/app/batch/${encodeURIComponent(row.batch_no)}">${frappe.utils.escape_html(row.batch_no)}</a></td>
				<td>${flt(row.quantity, 6)} ${frappe.utils.escape_html(row.uom || "")}</td></tr>`).join("");
			wrapper.html(rows
				? `<table class="table table-bordered"><thead><tr><th>${__("Input Material")}</th><th>${__("Actual Input Batch")}</th><th>${__("Consumed Quantity")}</th></tr></thead><tbody>${rows}</tbody></table>`
				: `<div class="text-muted">${__("Actual input batches are linked when the existing manufacturing Stock Entry is submitted.")}</div>`);
		} catch (error) {
			wrapper.html(`<div class="text-danger">${__("Recorded input batches could not be loaded.")}</div>`);
		}
	},
});

function install_lot_quantity_formatters(frm) {
	if (!["m", "meter", "meters", "metre", "metres"].includes(String(frm.doc.quantity_uom || "").trim().toLowerCase())) return;
	for (const fieldname of [
		"planned_quantity", "actual_output_quantity", "input_quantity", "produced_quantity",
		"good_quantity", "rejected_quantity", "rework_quantity", "usable_scrap", "unusable_scrap",
	]) {
		const field = frm.fields_dict[fieldname];
		if (!field?.df) continue;
		field.df.precision = "0";
		if (field.df.read_only) {
			field.df.formatter = (value) => {
				const number = Number(value);
				return Number.isFinite(number) ? Math.floor(Math.max(number, 0) + 0.5).toLocaleString() : "0";
			};
		}
		frm.refresh_field(fieldname);
	}
}
