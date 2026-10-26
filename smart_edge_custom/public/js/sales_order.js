frappe.ui.form.on("Sales Order", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.status === "Cancelled" || !frappe.model.can_create("Final EB BOM Generator")) {
			return;
		}

		const overview = await get_manufacturing_overview(frm.doc.name);
		if (!overview.has_eligible_items) return;

		frm.add_custom_button(__("Plan Manufacturing"), () => open_manufacturing_dialog(frm), __("Create"));
		if ((overview.linked_generators || []).length) {
			frm.add_custom_button(__("View Manufacturing Plans"), () => {
				frappe.set_route("List", "Final EB BOM Generator", { sales_order: frm.doc.name });
			});
		}
	},
});

async function get_manufacturing_overview(sales_order) {
	const response = await frappe.call({
		method: "smart_edge_custom.manufacturing.sales_order_planning.get_sales_order_manufacturing_overview",
		args: { sales_order },
	});
	return response.message || { items: [] };
}

async function open_manufacturing_dialog(frm) {
	const overview = await get_manufacturing_overview(frm.doc.name);
	let selected_default = false;
	const rows = (overview.items || []).map((row) => {
		const select = !selected_default && row.manufacturing_shortage > 0 ? 1 : 0;
		if (select) selected_default = true;
		return {
			...row,
			select,
			item_display: [row.item_code, row.item_name].filter(Boolean).join(" — "),
			uom_display: row.uom === row.stock_uom ? row.stock_uom : `${row.uom} / ${row.stock_uom}`,
			availability: row.stock_is_estimate ? __("Estimate") : __("Reserved/Available"),
		};
	});
	if (!rows.length) {
		frappe.msgprint(__("This Sales Order has no eligible manufactured finished EB items."));
		return;
	}

	let creating = false;
	const dialog = new frappe.ui.Dialog({
		title: __("Plan Manufacturing from Sales Order"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "availability_note",
				options: `<div class="text-muted small mb-2">${frappe.utils.escape_html(overview.availability_note || "")}</div>`,
			},
			{
				fieldtype: "Table",
				fieldname: "items",
				label: __("Eligible Sales Order Items"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				data: rows,
				fields: [
					{ fieldtype: "Check", fieldname: "select", label: __("Plan"), in_list_view: 1, columns: 1 },
					{ fieldtype: "Data", fieldname: "item_display", label: __("Item"), read_only: 1, in_list_view: 1, columns: 2 },
					{ fieldtype: "Float", fieldname: "ordered_qty", label: __("Ordered"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "delivered_qty", label: __("Delivered"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "outstanding_qty", label: __("Outstanding"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "available_fg_stock", label: __("FG Stock"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "existing_planned_qty", label: __("Planned"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "manufacturing_shortage", label: __("Shortage"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Float", fieldname: "quantity_to_manufacture", label: __("Manufacture"), reqd: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Data", fieldname: "uom_display", label: __("Sales / Mfg UOM"), read_only: 1, in_list_view: 1, columns: 1 },
					{ fieldtype: "Data", fieldname: "availability", label: __("Availability"), read_only: 1, in_list_view: 1, columns: 1 },
				],
			},
		],
		primary_action_label: __("Create Final EB BOM Generator"),
		primary_action: async () => {
			if (creating) return;
			const selected = (dialog.fields_dict.items.grid.get_data() || []).filter((row) => cint(row.select));
			if (selected.length !== 1) {
				frappe.msgprint(__("Select one item with an uncovered manufacturing shortage. You can plan additional items afterward."));
				return;
			}
			for (const row of selected) {
				if (flt(row.quantity_to_manufacture) <= 0 || flt(row.quantity_to_manufacture) > flt(row.manufacturing_shortage)) {
					frappe.throw(__("Quantity to Manufacture for {0} must be greater than zero and cannot exceed {1} {2}.", [
						row.item_code,
						row.manufacturing_shortage,
						row.stock_uom,
					]));
				}
			}

			creating = true;
			const $primary = dialog.get_primary_btn();
			$primary.prop("disabled", true);
			try {
				const created = [];
				for (const row of selected) {
					const response = await frappe.call({
						method: "smart_edge_custom.manufacturing.sales_order_planning.create_final_eb_bom_generator",
						args: {
							sales_order: frm.doc.name,
							sales_order_item: row.sales_order_item,
							quantity: row.quantity_to_manufacture,
						},
					});
					if (response.message?.name) created.push(response.message.name);
				}
				dialog.hide();
				if (created.length === 1) {
					frappe.set_route("Form", "Final EB BOM Generator", created[0]);
				} else {
					frappe.msgprint({
						title: __("Manufacturing Plans Created"),
						message: created.map((name) => `<a href="/app/final-eb-bom-generator/${encodeURIComponent(name)}">${frappe.utils.escape_html(name)}</a>`).join("<br>"),
						indicator: "green",
					});
					await frm.reload_doc();
				}
			} finally {
				creating = false;
				$primary.prop("disabled", false);
			}
		},
	});
	dialog.show();
}
