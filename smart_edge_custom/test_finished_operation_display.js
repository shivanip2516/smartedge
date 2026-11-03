const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({
	console, setTimeout,
	__: (message, args = []) => message.replace(/\{(\d+)\}/g, (_, index) => args[index]),
	flt: (value) => Number(value) || 0,
	cint: (value) => Number(value) || 0,
	smart_edge_custom: {},
	frappe: { pages: { "smartedge-production-planning": {}, "smartedge-batch-genealogy": {} }, provide() {} },
});
const read = (path) => fs.readFileSync(require.resolve(path), "utf8");
vm.runInContext(read("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js"), context);
const board = Object.create(context.smart_edge_custom.ProductionPlanningBoard.prototype);
const final = ["Extrusion", "Printing", "Packing"].map((operation, index) => ({
	id: "PLAN-" + index, production: "GEN", execution_key: "production:GEN:PLAN-0", plan_type: "Final EB",
	operation, operation_sequence: index + 1, status: "Pending", finished_item: "FG-ITEM", item_name: "Finished EB Item Name",
}));
const materials = ["Compound", "Base Colour"].map((material_type, index) => ({
	id: "MAT-" + index, production: "GEN", execution_key: "production:GEN:PLAN-0", plan_type: "Required Material",
	material_type, operation: "Mixing", status: "Pending", operation_sequence: 0,
}));
board.state = { board: { all_operations: [...final, ...materials, final[0]] }, filters: {} };
const groups = board.get_item_groups();
assert.equal(groups.length, 1);
const finished = groups.find((group) => group.parent.plan_type === "Final EB");
assert.equal(finished.total_count, 5);
assert.deepEqual(Array.from(finished.operations.filter((row) => row.plan_type === "Final EB"), (row) => row.operation), ["Extrusion", "Printing", "Packing"]);
assert.equal(finished.parent.finished_item, "FG-ITEM");
assert.equal(finished.parent.item_name, "Finished EB Item Name");
assert.equal(groups.filter((group) => group.parent.plan_type === "Required Material").length, 0);
assert.equal(finished.operations.filter((row) => row.plan_type === "Required Material").length, 2);

// Render the existing details panel; lot references must not generate new cards.
context.frappe.utils = { escape_html: (value) => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;") };
context.$ = (markup) => ({ markup, find() { return this; }, on() { return this; } });
board.expanded_operation_rows = new Set();
board.bind_more_menu = () => {};
board.get_status_line_html = () => "";
board.get_primary_action_html = () => "";
const details = board.make_operation_row({ ...final[2], status: "Completed",
	production_job: "SE-JOB-2026-00034", actual_operation_sequence: 3,
	production_lot: "PCK-SE-JOB-2026-00034-03", previous_operation_lot: "QC-SE-JOB-2026-00033-02",
	operation_trace_id: "PCK-SE-JOB-2026-00034-03",
	related_finished_goods_batches: ["FG-001"], planned_quantity: 500, lot_actual_output_quantity: 490,
	lot_uom: "Kg",
}).markup;
for (const text of ["Packing", "FG-ITEM", "Finished EB Item Name", "SE-JOB-2026-00034",
	"PCK-SE-JOB-2026-00034-03", "QC-SE-JOB-2026-00033-02", "FG-001", "500 m", "490 m"]) {
	assert.ok(details.includes(text), `Operation details missing: ${text}`);
}
assert.match(details, /\/app\/batch\/FG-001/);
assert.match(details, /Operation Sequence<\/span><strong>3<\/strong>/);
assert.doesNotMatch(details, /Operation Trace ID/);
assert.match(details, /\/app\/smartedge-production-lot\/PCK-SE-JOB-2026-00034-03/);
const collapsedHeader = details.split('<div class="se-operation-details-panel"')[0];
assert.match(collapsedHeader, /\/app\/smartedge-production-lot\/PCK-SE-JOB-2026-00034-03/,
	"Saved lot link must remain visible when operation details are collapsed after refresh");
const missingDetails = board.make_operation_row({ ...final[1], status: "Completed", operation_lot_missing: true }).markup;
assert.match(missingDetails, /Missing record/);
assert.doesNotMatch(missingDetails, /\/app\/smartedge-production-lot\//);
assert.doesNotMatch(board.make_operation_row(materials[0]).markup, /Finished Goods Item/);
assert.equal(board.get_item_groups().length, 1);
const originalBoard = board.state.board;
board.state.board = { week_start: "2026-10-26", week_end: "2026-11-01", all_operations: [
	...final.map((row) => ({ ...row, status: "Completed", planned_date: "2026-10-30" })),
	...materials.map((row) => ({ ...row, status: "Completed", planned_date: "2026-10-29" })),
] };
const completedGroups = board.get_item_groups();
assert.equal(completedGroups.length, 1, "The parent card must remain after Packing completion");
assert.equal(completedGroups[0].status, "Completed");
assert.equal(completedGroups[0].column_key, "2026-10-30");
assert.equal(completedGroups[0].operations.length, 5);
assert.equal(completedGroups[0].parent.plan_type, "Final EB");
board.state.board = originalBoard;
assert.match(read("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.css"),
	/\.se-operation-lot-id\s*\{[^}]*display:\s*block;[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere;/);

vm.runInContext(read("./smart_edge_custom/page/smartedge_batch_genealogy/smartedge_batch_genealogy.js"), context);
vm.runInContext("globalThis.renderLot = SmartEdgeBatchGenealogy.prototype.render_lot", context);
const renderer = {
	link: (_, name, label) => `<a>${label || name}</a>`, escape: String, number: (value) => String(value || 0),
	render_batch: (node) => `<span>${node.batch_no}</span>`,
};
renderer.render_lot = (node, depth) => context.renderLot.call(renderer, node, depth);
const html = renderer.render_lot({ production_lot: "EXT-2026-00001", finished_item: "FG-ITEM", finished_item_name: "Finished EB Item Name",
	operation: "Extrusion", planned_quantity: 500, actual_output_quantity: 490, produced_quantity: 480,
	quantity_uom: "Meter", children: [], stock_batch_genealogy: [{ batch_no: "CMP-EXISTING" }],
}, 0);
assert.match(html, /Finished EB Item Name/);
assert.match(html, /Planned: 500/);
assert.match(html, /Actual Output: 490/);
assert.match(html, /CMP-EXISTING/);

let lotHandlers;
context.frappe.ui = { form: { on: (_, handlers) => { lotHandlers = handlers; } } };
context.frappe.utils = { escape_html: String };
context.frappe.call = async () => ({ message: { input_batches: [
	{ batch_no: "CMP-EXISTING", item_name: "Compound 1", quantity: 173, uom: "Kg" },
	{ batch_no: "BC-EXISTING", item_name: "Base Colour", quantity: 300, uom: "Gram" },
] } });
vm.runInContext(read("./smart_edge_custom/doctype/smartedge_production_lot/smartedge_production_lot.js"), context);
let rendered;
lotHandlers.refresh({ doc: { name: "PCK-2026-00001" }, is_new: () => false,
	fields_dict: { traceability_details: { $wrapper: { html: (value) => { rendered = value; } } } },
}).then(() => {
	assert.match(rendered, /CMP-EXISTING/);
	assert.match(rendered, /BC-EXISTING/);
	assert.match(rendered, /300 Gram/);
	console.log("Finished operation display checks passed: one parent card with three FG operations and two material plans, Item name, planned/actual quantities and recorded input batches.");
}).catch((error) => { console.error(error); process.exitCode = 1; });
