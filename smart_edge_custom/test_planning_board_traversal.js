const assert = require("node:assert/strict");

global.smart_edge_custom = {};
global.__ = (value) => value;
global.frappe = {
	pages: { "smartedge-production-planning": {} },
	provide: () => {},
};

const {
	get_operation_display_label,
	resolve_item_group_placement,
} = require("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js");

function operation(id, sequence, status = "Pending", planned_date = null) {
	return { id, operation_sequence: sequence, status, planned_date };
}

function placement(operations) {
	return resolve_item_group_placement(operations);
}

assert.equal(
	get_operation_display_label({
		plan_type: "Required Material",
		material_type: "Compound",
		item_name: "M3-M6",
		finished_item: "IT-000026",
		bom_no: "BOM-IT-000026-001",
		operation: "Mixing",
	}),
	"Compound MIXING — M3-M6"
);
assert.equal(
	get_operation_display_label({
		plan_type: "Required Material",
		material_type: "Base Colour",
		item_name: "Pumic Grey",
		finished_item: "IT-000027",
		bom_no: "BOM-IT-000027-001",
		operation: "Mixing",
	}),
	"Base Colour MIXING — Pumic Grey"
);
assert.equal(
	get_operation_display_label({
		plan_type: "Required Material",
		material_type: "Compound",
		finished_item: "IT-000026",
		bom_no: "BOM-IT-000026-001",
		operation: "Mixing",
	}),
	"Compound MIXING — IT-000026"
);
assert.equal(
	get_operation_display_label({ plan_type: "Final EB", operation: "Packing" }),
	"Packing"
);
for (const [compound_workstation, colour_workstation] of [["Mixer-1", "Mixer-1"], ["Mixer-1", "Mixer-2"]]) {
	assert.equal(
		get_operation_display_label({
			plan_type: "Required Material",
			material_type: "Compound",
			item_name: "M3-M6",
			workstation: compound_workstation,
		}),
		"Compound MIXING — M3-M6"
	);
	assert.equal(
		get_operation_display_label({
			plan_type: "Required Material",
			material_type: "Base Colour",
			item_name: "Pumic Grey",
			workstation: colour_workstation,
		}),
		"Base Colour MIXING — Pumic Grey"
	);
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
