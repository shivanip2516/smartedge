const assert = require("node:assert/strict");
const fs = require("node:fs");

const read_json = (path) => JSON.parse(fs.readFileSync(require.resolve(path), "utf8"));
const parent = read_json("./smart_edge_custom/doctype/smartedge_workstation_capacity_rate/smartedge_workstation_capacity_rate.json");
const child = read_json("./smart_edge_custom/doctype/smartedge_workstation_capacity_rate_row/smartedge_workstation_capacity_rate_row.json");

assert.equal(parent.field_order.at(-1), "capacity_rows");
assert.equal(parent.fields.find((field) => field.fieldname === "capacity_rows").options, "SmartEdge Workstation Capacity Rate Row");
assert.equal(parent.fields.find((field) => field.fieldname === "matching_section").label, "Optional Product / Size / Route Matching");
assert.deepEqual(child.field_order, ["product_size", "capacity_rate", "capacity_uom"]);
for (const fieldname of child.field_order) {
	const field = child.fields.find((candidate) => candidate.fieldname === fieldname);
	assert.ok(field, `${fieldname} must exist in the child DocType`);
	assert.equal(field.in_list_view, 1, `${fieldname} must be visible in the grid`);
}

console.log("Workstation capacity rate grid metadata checks passed");
