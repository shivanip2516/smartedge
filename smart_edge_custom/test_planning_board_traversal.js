const assert = require("node:assert/strict");

global.smart_edge_custom = {};
global.__ = (value) => value;
global.frappe = {
	pages: { "smartedge-production-planning": {} },
	provide: () => {},
};

const {
	get_card_status,
	get_group_qc_status,
	get_invalid_completion_output,
	get_operation_display_label,
	resolve_item_group_placement,
} = require("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js");

assert.equal(get_invalid_completion_output({
	good_qty: 950,
	rejected_qty: 20,
	rework_qty: 10,
	usable_scrap: 15,
	unusable_scrap: 5,
}), null);
assert.equal(get_invalid_completion_output({ rejected_qty: -5 }), "Rejected Output");
assert.equal(get_invalid_completion_output({ usable_scrap: "not-a-number" }), "Usable Scrap");

assert.equal(
	get_card_status({ status: "Completed", job_status: "Completed", qc_display_status: "QC Pending" }),
	"Completed",
	"QC visibility must not replace the production status"
);
assert.equal(
	get_group_qc_status([
		{ qc_required: true, qc_display_status: "QC Approved" },
		{ qc_required: true, qc_display_status: "QC Pending" },
	]),
	"QC Pending"
);

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
	assert.equal(placement(operations).column_key, "2026-11-03");
	assert.equal(resolve_item_group_placement(operations, "2026-10-26", "2026-11-01").column_key, "2026-10-27");
	assert.equal(resolve_item_group_placement(operations, "2026-11-09", "2026-11-15"), null);
}

assert.equal(placement([operation("CANCELLED", 1, "Cancelled", "2026-10-27")]), null);
assert.equal(placement([operation("UNDATED", 1, "Completed")]), null);

console.log("Planning board traversal tests passed");
