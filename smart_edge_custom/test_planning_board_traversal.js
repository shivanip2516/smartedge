const assert = require("node:assert/strict");

global.smart_edge_custom = {};
global.frappe = {
	pages: { "smartedge-production-planning": {} },
	provide: () => {},
};

const {
	resolve_item_group_placement,
} = require("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js");

function operation(id, sequence, status = "Pending", planned_date = null) {
	return { id, operation_sequence: sequence, status, planned_date };
}

function placement(operations) {
	return resolve_item_group_placement(operations);
}

{
	const operations = [operation("OP-1", 1), operation("OP-2", 2), operation("OP-3", 3)];
	assert.equal(placement(operations).column_key, "pending");
}

{
	const operations = [
		operation("OP-1", 1, "Planned", "2026-10-27"),
		operation("OP-2", 2, "Planned", "2026-10-28"),
		operation("OP-3", 3),
	];
	assert.equal(placement(operations).column_key, "2026-10-27");
	operations[0].status = "Completed";
	assert.equal(placement(operations).column_key, "2026-10-28");
}

{
	const operations = [
		operation("OP-1", 1, "Planned", "2026-10-27"),
		operation("OP-2", 2, "Planned", "2026-10-27"),
		operation("OP-3", 3, "Planned", "2026-10-28"),
	];
	operations[0].status = "Completed";
	assert.equal(placement(operations).column_key, "2026-10-27");
	operations[1].status = "Completed";
	assert.equal(placement(operations).column_key, "2026-10-28");
}

{
	const operations = [
		operation("OP-1", 1, "Completed", "2026-10-27"),
		operation("OP-2", 2),
		operation("OP-3", 3),
	];
	assert.equal(placement(operations).column_key, "pending");
}

{
	const operations = [
		operation("OP-1", 1, "In Progress", "2026-10-27"),
		operation("OP-2", 2, "Planned", "2026-10-28"),
	];
	assert.equal(placement(operations).column_key, "2026-10-27");
	operations[0].status = "Completed";
	operations[1].status = "On Hold";
	assert.equal(placement(operations).column_key, "2026-10-28");
}

{
	const operations = [
		operation("OP-1", 1, "Completed", "2026-10-27"),
		operation("OP-2", 2, "Planned", "2026-11-03"),
	];
	assert.equal(placement(operations).column_key, "2026-11-03");
	operations[1].status = "Completed";
	assert.equal(placement(operations), null);
}

console.log("Planning board traversal tests passed");
