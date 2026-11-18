frappe.ui.form.on("SmartEdge Production Plan Entry", {
	refresh(frm) {
		for (const fieldname of ["production_quantity", "planned_meter", "released_qty_meter"]) {
			const field = frm.fields_dict[fieldname];
			if (!field?.df?.read_only) continue;
			field.df.precision = "0";
			field.df.formatter = (value) => format_meter(value);
			frm.refresh_field(fieldname);
		}
	},
});

function format_meter(value) {
	const number = Number(value);
	if (!Number.isFinite(number)) return "0";
	return number.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
