const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.js"), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function stock(material, available = 1) {
	return { available, materials: [{ item_code: material === "Compound" ? "ADDITIVE" : "PIGMENT", uom: material === "Compound" ? "Kg" : "Gram", required_qty: 66, available_qty: available ? 100 : 10, shortage_qty: available ? 0 : 56 }] };
}

async function open(check) {
	let dialog;
	const requests = [];
	const requirements = ["Compound", "Base Colour"].map((material_type) => ({
		material_type, item_code: material_type === "Compound" ? "COMPOUND-ITEM" : "COLOUR-ITEM",
		batches_to_plan: 3, required_batches: 3, stock_uom: material_type === "Compound" ? "Kg" : "Gram",
		bom_no: "BOM-" + material_type, operation: "Mixing", operation_options: ["Mixing"],
		default_workstation: "Mixer-1", compatible_workstations: ["Mixer-1"],
		source_warehouse: "Stores - SE", wip_warehouse: "Work In Progress - SE", target_warehouse: "Stores - SE",
	}));
	class Wrapper {
		html(value) { this.markup = value; }
		append(value) { this.markup += value; }
		find() { return { on: (_, callback) => { this.retry = callback; } }; }
	}
	class Dialog {
		constructor(options) {
			dialog = this;
			this.fields_dict = {};
			this.values = {};
			for (const field of options.fields.filter((field) => field.fieldname)) {
				this.fields_dict[field.fieldname] = { df: field, $wrapper: new Wrapper() };
				this.values[field.fieldname] = field.fieldname.endsWith("_employee") ? "EMPLOYEE" : field.default;
			}
		}
		show() {}
		disable_primary_action() { this.disabled = true; }
		enable_primary_action() { this.disabled = false; }
		get_values() { return this.values; }
		get_value(field) { return this.values[field]; }
	}
	const context = vm.createContext({
		console, setTimeout,
		__: (message, args = []) => message.replace(/\{(\d+)\}/g, (_, index) => args[index]),
		flt: (value) => Number(value) || 0,
		frappe: {
			ui: { form: { on() {} }, Dialog },
			utils: { escape_html: (value) => String(value).replaceAll("<", "&lt;") },
			datetime: { now_datetime: () => "2026-10-30 10:00:00" },
			call: async (request) => {
				if (request.method.endsWith("get_material_planning_context")) return { message: { requirements } };
				requests.push(request.args);
				return { message: await check(request.args) };
			},
		},
	});
	vm.runInContext(source, context);
	await context.open_material_planning_dialog({ doc: { name: "FEBG-TEST", company: "Smart Edge" }, is_dirty: () => false });
	await flush();
	return { dialog, requests };
}

async function run() {
	const both = await open((args) => stock(args.material_type));
	assert.equal(both.requests.length, 2);
	assert.equal(both.dialog.disabled, false);
	assert.match(both.dialog.fields_dict.compound_raw_stock_status.$wrapper.markup, /ADDITIVE/);
	assert.match(both.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /PIGMENT/);
	assert.match(both.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /66 Gram/);
	assert.equal(both.requests[1].material_type, "Base Colour");
	assert.equal(both.requests[1].production, "FEBG-TEST");
	assert.equal(both.requests[1].source_warehouse, "Stores - SE");

	let fail = true;
	const errors = await open((args) => {
		if (args.material_type === "Base Colour" && fail) {
			throw { responseJSON: { _server_messages: JSON.stringify([JSON.stringify({ message: "No active submitted Base Colour BOM" })]) } };
		}
		return stock(args.material_type);
	});
	const errorWrapper = errors.dialog.fields_dict.base_colour_raw_stock_status.$wrapper;
	assert.match(errorWrapper.markup, /No active submitted Base Colour BOM/);
	assert.doesNotMatch(errorWrapper.markup, /Checking raw-material/);
	assert.match(errorWrapper.markup, /Retry stock check/);
	assert.equal(errors.dialog.disabled, true);
	assert.match(errors.dialog.fields_dict.compound_raw_stock_status.$wrapper.markup, /ADDITIVE/);
	fail = false;
	await errorWrapper.retry();
	assert.match(errorWrapper.markup, /PIGMENT/);
	assert.equal(errors.dialog.disabled, false);

	const network = await open((args) => {
		if (args.material_type === "Base Colour") throw { status: 0 };
		return stock(args.material_type);
	});
	assert.match(network.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /could not be checked/);
	assert.doesNotMatch(network.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /Checking raw-material/);

	const shortage = await open((args) => stock(args.material_type, args.material_type === "Compound" ? 1 : 0));
	assert.match(shortage.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /Insufficient BOM raw materials/);
	assert.match(shortage.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /10 Gram/);
	assert.equal(shortage.dialog.disabled, true);

	const configuration = await open((args) => args.material_type === "Base Colour"
		? { ...stock(args.material_type), available: 0, message: "Base Colour Item is not batch-controlled" }
		: stock(args.material_type));
	assert.match(configuration.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /PIGMENT/);
	assert.match(configuration.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /not batch-controlled/);
	assert.equal(configuration.dialog.disabled, true);

	const missing = await open((args) => args.material_type === "Base Colour"
		? { available: 0, materials: [], message: "No transferable Pigment raw materials" }
		: stock(args.material_type));
	assert.match(missing.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup, /No transferable Pigment/);
	assert.equal(missing.dialog.disabled, true);

	const pending = [];
	const racing = await open((args) => args.material_type === "Base Colour"
		? new Promise((resolve) => pending.push({ resolve, args })) : stock(args.material_type));
	const field = racing.dialog.fields_dict.base_colour_source;
	await field.df.onchange();
	assert.equal(pending.length, 1, "Duplicate in-flight requests must be suppressed");
	racing.dialog.values.base_colour_source = "Other Stores - SE";
	const second = field.df.onchange();
	assert.equal(pending.length, 2);
	pending[1].resolve({ available: 0, materials: [], message: "Current warehouse has no stock" });
	await second;
	pending[0].resolve(stock("Base Colour"));
	await flush();
	const markup = racing.dialog.fields_dict.base_colour_raw_stock_status.$wrapper.markup;
	assert.match(markup, /Current warehouse has no stock/);
	assert.doesNotMatch(markup, /Checking raw-material/);
	assert.equal(racing.dialog.disabled, true);
	console.log("Material availability dialog checks passed: both materials, failures, retry, configuration errors, deduplication and stale responses.");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
