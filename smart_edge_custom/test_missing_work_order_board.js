const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const context = vm.createContext({
	console, setTimeout, __: (value) => value, flt: (value) => Number(value) || 0, cint: (value) => Number(value) || 0,
	smart_edge_custom: {},
	frappe: { pages: { "smartedge-production-planning": {} }, provide() {},
		utils: { escape_html: (value) => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;") } },
});
vm.runInContext(fs.readFileSync(require.resolve("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js"), "utf8"), context);
const board = Object.create(context.smart_edge_custom.ProductionPlanningBoard.prototype);
board.state = { filters: {} };
let summary;
board.$summary = { html(value) { summary = value; } };
board.render_summary({ all_operations: [], work_order_warnings: [{
	message: "Work Order WO-DELETED referenced by Production Plan PLAN-STALE is missing. Review correction.",
	plan_entries: ["PLAN-STALE"],
}] });
assert.doesNotMatch(summary, /WO-DELETED|PLAN-STALE|role="alert"/);
const unavailable = board.get_primary_action_html({ status: "Pending", work_order_missing: true, work_order_warning: "WO-DELETED missing" }, true, "");
assert.match(unavailable, /disabled/);
assert.match(unavailable, /Unavailable/);
assert.doesNotMatch(unavailable, /se-plan|se-start|se-complete/);
assert.match(board.get_primary_action_html({ status: "Pending" }, true, ""), /se-plan/);
assert.match(board.get_primary_action_html({ status: "Ready", start_allowed: true }, false, ""), /se-start/);
assert.match(board.get_primary_action_html({ status: "In Progress" }, false, ""), /se-complete/);
context.$ = (markup) => ({ markup, find() { return this; }, on() { return this; } });
board.expanded_operation_rows = new Set();
board.bind_more_menu = () => {};
const local = board.make_operation_row({ id: "PLAN-STALE", plan_type: "Required Material",
	status: "Pending", work_order_missing: true, work_order_warning: "WO-DELETED missing", operation: "Mixing" }).markup;
assert.match(local, /WO-DELETED missing/);
assert.match(local, /Unavailable/);
if (process.argv[2]) {
	const response = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
	board.render_summary(response);
	assert.doesNotMatch(summary, /MFG-WO-2026-00034|SE-PLAN-2026-00061|alert-warning/);
	board.state.board = response;
	const groups = board.get_item_groups();
	assert.ok(groups.some((group) => group.operations.some((card) => card.work_order === "MFG-WO-2026-00035")));
	assert.ok(groups.some((group) => group.operations.some((card) => card.work_order === "MFG-WO-2026-00036")));
	assert.ok(groups.every((group) => group.parent.plan_type === "Final EB"));
}
console.log("Clean board summary, local missing-reference error, unavailable actions and valid grouping checks passed.");
