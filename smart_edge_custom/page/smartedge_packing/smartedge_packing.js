frappe.pages["smartedge-packing"].on_page_load = function (wrapper) {
	const is_meter_uom = (uom) => ["m", "meter", "meters", "metre", "metres"].includes(String(uom || "").trim().toLowerCase());
	const round_meter = (value) => Math.floor(Math.max(Number(value) || 0, 0) + 0.5);
	const format_quantity = (value, uom) => is_meter_uom(uom)
		? round_meter(value).toLocaleString()
		: String(value == null ? 0 : value);
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("SmartEdge Packing"), single_column: true });
	const body = $("<div class='p-4'></div>").appendTo(page.body);
	body.append(`<div class='row'>
		<div class='col-md-3'><label>${__("Customer")}</label><input class='form-control se-customer'></div>
		<div class='col-md-3'><label>${__("Sales Order Item")}</label><input class='form-control se-sales-order-item'></div>
		<div class='col-md-3'><label>${__("Warehouse")}</label><input class='form-control se-warehouse'></div>
		<div class='col-md-3'><label>${__("Packing Session")}</label><input class='form-control se-session' readonly></div>
	</div>
	<div class='row'><div class='col-md-3'><label>${__("FG Batch")}</label><input class='form-control se-batch'></div><div class='col-md-3'><label>${__("Production Lot")}</label><input class='form-control se-lot'></div><div class='col-md-2'><label>${__("Item")}</label><input class='form-control se-item'></div><div class='col-md-2'><label>${__("Quantity")}</label><input class='form-control se-qty' type='number' step='any'></div><div class='col-md-2 d-flex align-items-end'><button class='btn btn-primary se-confirm-pack w-100'>${__("Confirm Pack")}</button></div></div>
	<div class='mt-3'><button class='btn btn-default se-new-session'>${__("New Session")}</button> <button class='btn btn-default se-confirm-session'>${__("Confirm Session")}</button> <button class='btn btn-primary se-create-dn'>${__("Create Delivery Note")}</button></div>
	<div class='se-packing-result mt-3'></div>`);
	const result = body.find(".se-packing-result");
	const show_error = (error) => result.html(`<div class='alert alert-danger'>${frappe.utils.escape_html(error.message || __("Unable to complete packing action"))}</div>`);
	const call = async (method, args) => (await frappe.call({ method, args })).message;
	body.find(".se-new-session").on("click", async () => {
		try {
			const row = await call("smart_edge_custom.manufacturing.packing.create_packing_session", { customer: body.find(".se-customer").val(), warehouse: body.find(".se-warehouse").val() });
			body.find(".se-session").val(row.name);
			result.html(`<div class='alert alert-info'>${__("Packing Session {0} created.", [frappe.utils.escape_html(row.name)])}</div>`);
		} catch (error) { show_error(error); }
	});
	body.find(".se-confirm-pack").on("click", async () => {
		try {
			const quantity_input = body.find(".se-qty");
			const packing_uom = quantity_input.data("uom");
			const packed_qty = is_meter_uom(packing_uom) ? round_meter(quantity_input.val()) : quantity_input.val();
			if (is_meter_uom(packing_uom)) quantity_input.val(packed_qty);
			const row = await call("smart_edge_custom.manufacturing.packing.confirm_pack", { session: body.find(".se-session").val(), sales_order_item: body.find(".se-sales-order-item").val(), item_code: body.find(".se-item").val(), fg_batch: body.find(".se-batch").val(), production_lot: body.find(".se-lot").val(), packed_qty, warehouse: body.find(".se-warehouse").val() });
			result.html(`<div class='alert alert-success'>${__("Pack {0} confirmed.", [frappe.utils.escape_html(row.pack_id)])}</div>`);
		} catch (error) { show_error(error); }
	});
	body.find(".se-confirm-session").on("click", async () => {
		try { await call("smart_edge_custom.manufacturing.packing.confirm_packing_session", { session: body.find(".se-session").val() }); result.html(`<div class='alert alert-success'>${__("Packing Session confirmed.")}</div>`); } catch (error) { show_error(error); }
	});
	body.find(".se-create-dn").on("click", async () => {
		try { const row = await call("smart_edge_custom.manufacturing.packing.create_delivery_note", { session: body.find(".se-session").val() }); result.html(`<div class='alert alert-success'>${__("Draft Delivery Note {0} created.", [frappe.utils.escape_html(row.delivery_note)])}</div>`); } catch (error) { show_error(error); }
	});
};
