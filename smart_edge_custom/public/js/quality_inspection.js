frappe.ui.form.on("Quality Inspection", {
	refresh(frm) {
		if (!frm.doc.custom_smartedge_qc_key) return;
		for (const fieldname of [
			"custom_eligible_qty",
			"custom_inspected_qty",
			"custom_accepted_qty",
			"custom_rejected_qty",
			"custom_rework_qty",
		]) {
			const field = frm.fields_dict[fieldname];
			if (!field?.df) continue;
			field.df.precision = "0";
			if (field.df.read_only) field.df.formatter = format_smartedge_meter;
			frm.refresh_field(fieldname);
		}
	},
});

function format_smartedge_meter(value) {
	const number = Number(value);
	if (!Number.isFinite(number)) return "0";
	return Math.floor(Math.max(number, 0) + 0.5).toLocaleString();
}
