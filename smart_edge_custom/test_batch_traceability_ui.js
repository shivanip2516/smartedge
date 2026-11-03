const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const escape = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
let handlers, markup = "", indicator;
const trace = {
	root: { batch_no: "EXT", item_code: "FG", item_name: "<Finished>", stock_uom: "Kg",
		batch_purpose: "Operation Traceability", operation_status: "Completed", batch_quantity: 0,
		operation: "Extrusion", production_job: "JOB-EXT", operation_output_quantity: 100, operation_output_uom: "Meter", children: [] },
	parent_batches: ["CMP", "BASE"], child_batches: ["QC"], final_batches: ["FG-001"], stock_entries: ["STE-1"],
	edges: [{ source: "CMP", target: "EXT", evidence: "LEDGER-1", quantity: 20, uom: "Kg", quantity_type: "Consumed",
		source_document_type: "Stock Entry", source_document_id: "STE-1", source_row_reference: "ROW-1", source_warehouse: "Stores",
		input_quantity: 20, input_uom: "Kg", operation_output_quantity: 100, operation_output_uom: "Meter", basis: "Submitted manufacturing input" }],
};
const context = vm.createContext({
	__: (value) => value,
	format_number: (value) => String(value), flt: (value) => Number(value) || 0,
	frappe: { ui: { form: { on: (_doctype, value) => { handlers = value; } } }, pages: { "smartedge-batch-genealogy": {} },
		utils: { escape_html: escape, get_form_link: (doctype, name) => `/app/${doctype}/${encodeURIComponent(name)}` },
		call: async () => ({ message: { operation_batch_trace: trace } }) },
});
vm.runInContext(fs.readFileSync(require.resolve("./public/js/batch.js"), "utf8"), context);
const frm = { is_new: () => false, doc: { name: "EXT", item: "FG", item_name: "<Finished>", batch_qty: 0,
	custom_batch_purpose: "Operation Traceability" },
	page: { set_indicator: (label) => { indicator = label; } },
	dashboard: { parent: { find: () => ({ remove() {} }) }, show() {}, add_section: () => ({ html: (value) => { markup = value; } }) },
};
(async () => {
	await handlers.refresh(frm);
	assert.equal(indicator, "Completed — Traceability");
	for (const label of ["Not eligible", "Parent Input Batches", "Child Output Batches", "FG-001", "STE-1", "100 Meter", "Operation Output UOM", "Input Material Summary", "20 Kg", "Consumed", "ROW-1", "Stores"]) assert.ok(markup.includes(label), label);
	assert.ok(markup.includes("&lt;Finished&gt;"));
	assert.ok(!markup.includes("<Finished>"));
	frm.doc = { name: "QC", item: "FG", item_name: "Finished", batch_qty: 39.784206,
		custom_batch_purpose: "Physical Stock", custom_operation_lot: "QC-LOT", custom_operation_output_quantity: 39.784206 };
	context.frappe.call = async () => ({ message: { operation_batch_trace: {
		root: { batch_no: "QC", stock_uom: "Meter", operation_output_quantity: 39.784206, operation_output_uom: "Meter" },
	} } });
	await handlers.refresh(frm);
	assert.equal(frm.doc.batch_qty, 39.784206);
	assert.ok((markup.match(/39\.784206 Meter/g) || []).length >= 2);
	frm.doc.batch_qty = 0;
	await handlers.refresh(frm);
	assert.ok(markup.includes("0 Meter"));
	assert.ok(markup.includes("39.784206 Meter"));
	assert.equal(frm.doc.custom_operation_output_quantity, 39.784206);
	frm.doc = { name: "CMP", item: "COMPOUND", item_name: "Compound", batch_qty: 0, custom_batch_purpose: "Physical Stock" };
	context.frappe.call = async () => ({ message: { root: { events: [{ stock_entry: "STE-MIX", children: [{ batch_no: "ADDITIVE" }] }] } } });
	await handlers.refresh(frm);
	assert.ok(markup.includes("ADDITIVE"));
	assert.ok(markup.includes("STE-MIX"));
	assert.ok(!markup.includes("Not eligible"));
	const resolvers = [];
	context.frappe.call = () => new Promise((done) => { resolvers.push(done); });
	const pending = handlers.refresh(frm);
	frm.doc.name = "DIFFERENT-BATCH";
	markup = "New document content";
	resolvers.forEach((done) => done({ message: { operation_batch_trace: trace } }));
	await pending;
	assert.equal(markup, "New document content");
	context.frappe.call = async () => { throw Error("Unavailable"); };
	await handlers.refresh(frm);
	assert.match(markup, /Unable to load verified genealogy/);
	vm.runInContext(fs.readFileSync(require.resolve("./smart_edge_custom/page/smartedge_batch_genealogy/smartedge_batch_genealogy.js"), "utf8"), context);
	const page = vm.runInContext("Object.create(SmartEdgeBatchGenealogy.prototype)", context);
	const html = page.render_operation_batch(trace.root, 0);
	assert.match(html, /Operation Traceability/);
	assert.match(html, /Completed/);
	assert.match(html, /100 Meter/);
	assert.match(html, /&lt;Finished&gt;/);
	console.log("Batch details and genealogy rendering: status, eligibility, quantities, links, escaping and error handling passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
