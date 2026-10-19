frappe.ui.form.on("Work Order", {
	refresh(frm) {
		refresh_batch_manufacturing(frm, false);
	},

	production_item(frm) {
		return refresh_batch_manufacturing(frm, true);
	},

	bom_no(frm) {
		return refresh_batch_manufacturing(frm, true);
	},

	custom_no_of_batches(frm) {
		set_total_manufacturing_quantity(frm);
	},
});

async function refresh_batch_manufacturing(frm, initialize) {
	if (!frm.doc.production_item || !frm.doc.bom_no) {
		set_batch_manufacturing_fields(frm, false);
		return;
	}

	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details",
		args: {
			item_code: frm.doc.production_item,
			bom_no: frm.doc.bom_no,
		},
	});
	const details = response.message || {};
	const confirmed_batch_work_order =
		flt(frm.doc.custom_weight_per_batch) > 0 && cint(frm.doc.custom_no_of_batches) > 0;
	set_batch_manufacturing_fields(
		frm,
		Boolean(details.applicable || confirmed_batch_work_order),
		details.stock_uom
	);

	if (!details.applicable || frm.doc.docstatus !== 0) {
		return;
	}

	// A refresh of an older draft must not silently replace its quantity.
	if (!initialize && !flt(frm.doc.custom_weight_per_batch)) {
		return;
	}

	await frm.set_value("custom_weight_per_batch", flt(details.weight_per_batch));
	if (cint(frm.doc.custom_no_of_batches) < 1) {
		await frm.set_value("custom_no_of_batches", 1);
	}
	await set_total_manufacturing_quantity(frm);
}

function set_batch_manufacturing_fields(frm, enabled, stock_uom) {
	frm.toggle_display("custom_no_of_batches", enabled);
	frm.toggle_display("custom_weight_per_batch", enabled);
	frm.toggle_reqd("custom_no_of_batches", enabled);
	frm.set_df_property("qty", "read_only", enabled ? 1 : 0);
	frm.set_df_property(
		"qty",
		"label",
		enabled && stock_uom
			? __("Total Manufacturing Quantity ({0})", [stock_uom])
			: __("Qty To Manufacture")
	);

	const label = stock_uom
		? __("Weight per Batch ({0})", [stock_uom])
		: __("Weight per Batch");
	frm.set_df_property("custom_weight_per_batch", "label", label);
}

async function set_total_manufacturing_quantity(frm) {
	const batches = cint(frm.doc.custom_no_of_batches);
	const weight = flt(frm.doc.custom_weight_per_batch);
	if (frm.doc.docstatus !== 0 || batches < 1 || weight <= 0) {
		return;
	}

	await frm.set_value("qty", flt(batches * weight, precision("qty", frm.doc)));
}

frappe.provide("smart_edge_custom.batch_manufacturing");

if (!smart_edge_custom.batch_manufacturing.standard_work_order_quantity_prompt) {
	smart_edge_custom.batch_manufacturing.standard_work_order_quantity_prompt =
		erpnext.work_order.show_prompt_for_qty_input;
	erpnext.work_order.show_prompt_for_qty_input = show_batch_aware_quantity_prompt;
}

async function show_batch_aware_quantity_prompt(frm, purpose, qty, additional_transfer_entry) {
	const standard_prompt = smart_edge_custom.batch_manufacturing.standard_work_order_quantity_prompt;
	if (!["Material Transfer for Manufacture", "Manufacture"].includes(purpose)) {
		return standard_prompt.call(this, frm, purpose, qty, additional_transfer_entry);
	}

	const details = await frappe.xcall(
		"smart_edge_custom.manufacturing.batch_manufacturing.get_work_order_batch_details",
		{
			item_code: frm.doc.production_item,
			bom_no: frm.doc.bom_no,
		}
	);
	const weight_per_batch = flt(frm.doc.custom_weight_per_batch || details?.weight_per_batch);
	const confirmed_batch_work_order =
		weight_per_batch > 0 && cint(frm.doc.custom_no_of_batches) > 0;
	if (!(details?.applicable || confirmed_batch_work_order)) {
		return standard_prompt.call(this, frm, purpose, qty, additional_transfer_entry);
	}

	const base_max = flt(
		additional_transfer_entry ? qty : this.get_max_transferable_qty(frm, purpose),
		precision("qty", frm.doc)
	);
	const stock_uom = details.stock_uom || __("Kg");
	let effective_max = base_max;
	let dialog;

	const complete_batches = () =>
		Math.max(0, Math.floor((effective_max + 0.000001) / weight_per_batch));
	const update_description = () => {
		if (!dialog) return;
		const batch_limit = complete_batches();
		const covered_qty = flt(batch_limit * weight_per_batch, precision("qty", frm.doc));
		const remainder = flt(effective_max - covered_qty, precision("qty", frm.doc));
		let description = __("Maximum complete batches: {0} ({1} {2})", [
			batch_limit,
			covered_qty,
			stock_uom,
		]);
		if (remainder > 0.000001) {
			description += " " +
				__("Remaining {0} {1} is not a complete batch and will not be silently transferred or manufactured.", [
					remainder,
					stock_uom,
				]);
		}
		dialog.fields_dict.number_of_batches.set_description(description);
	};
	const update_calculated_quantity = () => {
		if (!dialog) return;
		const batches = cint(dialog.get_value("number_of_batches"));
		dialog.set_value(
			"calculated_quantity",
			flt(batches * weight_per_batch, precision("qty", frm.doc))
		);
	};
	const set_default_batches = () => {
		if (!dialog) return;
		dialog.set_value("number_of_batches", complete_batches());
		update_description();
		update_calculated_quantity();
	};

	const batch_label =
		purpose === "Manufacture"
			? __("No. of Batches to Manufacture")
			: __("No. of Batches to Transfer");
	const fields = [
		{
			fieldname: "number_of_batches",
			fieldtype: "Int",
			label: batch_label,
			default: complete_batches(),
			reqd: 1,
			non_negative: 1,
			onchange: update_calculated_quantity,
		},
		{
			fieldname: "weight_per_batch",
			fieldtype: "Float",
			label: __("Weight per Batch ({0})", [stock_uom]),
			default: weight_per_batch,
			read_only: 1,
		},
		{
			fieldname: "calculated_quantity",
			fieldtype: "Float",
			label:
				purpose === "Manufacture"
					? __("Finished Manufacturing Quantity ({0})", [stock_uom])
					: __("Calculated Material Transfer Quantity ({0})", [stock_uom]),
			default: flt(complete_batches() * weight_per_batch, precision("qty", frm.doc)),
			read_only: 1,
		},
	];

	if (!additional_transfer_entry) {
		fields.push({
			fieldname: "consider_process_loss",
			fieldtype: "Check",
			label: __("Consider Process Loss"),
			default: 0,
			onchange() {
				effective_max = this.value ? base_max - flt(frm.doc.process_loss_qty) : base_max;
				set_default_batches();
			},
		});
	}

	return new Promise((resolve, reject) => {
		dialog = frappe.prompt(
			fields,
			(values) => {
				const batches = Number(values.number_of_batches);
				if (!Number.isInteger(batches) || batches < 1) {
					frappe.msgprint(__("No. of Batches must be a positive whole number."));
					reject();
					return;
				}

				const calculated_qty = flt(batches * weight_per_batch, precision("qty", frm.doc));
				const overproduction_qty =
					(flt(frm.doc.qty) * flt(frm.doc.__onload?.overproduction_percentage)) / 100;
				const allowed_qty = effective_max + overproduction_qty;
				if (calculated_qty > allowed_qty + 0.000001) {
					frappe.msgprint(__("Calculated quantity must not be more than {0} {1}.", [
						allowed_qty,
						stock_uom,
					]));
					reject();
					return;
				}

				values.qty = calculated_qty;
				values.purpose = purpose;
				resolve(values);
			},
			__("Select Batch Quantity"),
			__("Create")
		);
		update_description();
	});
}
