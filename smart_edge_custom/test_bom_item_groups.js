const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const handlers = {};
const messages = [];
const groups = { compound: "Compounds", colour: "Base Colour", additive: "Additives", pigment: "Pigments", finished: "Products" };
global.smart_edge_custom = { batch_manufacturing: {} };
global.__ = (message, args = []) => message.replace(/\{(\d+)\}/g, (_, index) => args[index]);
global.flt = (value) => Number(value) || 0;
const calculate = ({ method, args }) => {
	if (method.endsWith("get_bom_quantity")) {
		return { message: ["Compounds", "Base Colour"].includes(groups[args.item_code])
			? args.items.reduce((total, row) => total + flt(row.qty) * (row.conversion_factor || 1), 0)
			: null };
	}
	return { message: { is_batch_manufacturing_item: true, output_qty: 0 } };
};
global.frappe = {
	ui: { form: { on: (doctype, events) => { handlers[doctype] = events; }, get_event_handler_list: () => [] } },
	provide: () => {},
	db: { get_value: async (_, code) => ({ message: { item_group: groups[code] } }) },
	call: async (request) => calculate(request),
	utils: { escape_html: (value) => value },
	msgprint: (message) => messages.push(message),
};
vm.runInThisContext(fs.readFileSync(require.resolve("./public/js/bom.js"), "utf8"));

function form(item, component) {
	return {
		doc: { item, docstatus: 0, items: [{ idx: 1, item_code: component, qty: 20 }], quantity: 10 },
		set_query(field, table, callback) {
			assert.equal(field, "item_code");
			assert.equal(table, "items");
			this.query = callback;
		},
		refresh_field: () => {},
		toggle_display(field, value) { this[field + "_visible"] = value; },
		toggle_reqd: () => {},
		async set_value(field, value) { this.doc[field] = value; },
	};
}

async function run() {
	const parent = handlers.BOM;
	const child = handlers["BOM Item"];
	for (const [item, component] of [["compound", "additive"], ["colour", "pigment"]]) {
		const frm = form(item, component);
		parent.setup(frm);
		assert.deepEqual(frm.query().filters, { parent_item: item });
		await parent.item(frm);
		assert.equal(frm.doc.quantity, 20);
		assert.equal(frm.doc.custom_batch, 1);
		frm.doc.items.push({ idx: 2, item_code: component, qty: 30 });
		await child.items_add(frm);
		assert.equal(frm.doc.quantity, 50);
		frm.doc.items[1].qty = 40;
		await child.qty(frm);
		assert.equal(frm.doc.quantity, 60);
		frm.doc.items.shift();
		await child.items_remove(frm);
		assert.equal(frm.doc.quantity, 40);
		await parent.validate(frm);
		assert.equal(frm.doc.quantity, 40);
		await parent.refresh(frm);
		assert.equal(frm.doc.quantity, 40);
		frm.doc.items[0].conversion_factor = 0.001;
		await child.conversion_factor(frm);
		assert.equal(frm.doc.quantity, 0.04);
		frm.doc.docstatus = 1;
		frm.doc.quantity = 99;
		await parent.refresh(frm);
		assert.equal(frm.doc.quantity, 99);
	}

	const frm = form("compound", "additive");
	parent.setup(frm);
	const rows = frm.doc.items;
	frm.doc.item = "colour";
	await parent.item(frm);
	assert.equal(messages.length, 1);
	assert.match(messages[0].message, /Pigments.*Row 1: additive/);
	assert.equal(frm.doc.items, rows);
	assert.equal(frm.query().filters.parent_item, "colour");
	frm.doc.item = "finished";
	frm.doc.quantity = 10;
	await parent.item(frm);
	await child.qty(frm);
	await parent.refresh(frm);
	assert.equal(frm.doc.quantity, 10);
	assert.equal(messages.length, 1);
	frm.doc.custom_process_2 = "Extrusion";
	await parent.refresh(frm);
	assert.equal(frm.custom_process_2_visible, false);
	assert.equal(frm.doc.custom_process_2, "Extrusion");

	// A slow response for an older edit must not overwrite the current total.
	const racing = form("compound", "additive");
	const pending = [];
	frappe.call = (request) => new Promise((resolve) => pending.push(() => resolve(calculate(request))));
	const first = child.qty(racing);
	racing.doc.items[0].qty = 40;
	const second = child.qty(racing);
	pending[1]();
	await second;
	pending[0]();
	await first;
	assert.equal(racing.doc.quantity, 40);
	console.log("BOM client checks passed: quantity events, refresh, conversions, stale responses, submitted/Finished Product preservation, filtering and Process hiding.");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
