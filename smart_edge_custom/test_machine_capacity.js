const assert = require("node:assert/strict");

global.smart_edge_custom = {};
global.__ = (value) => value;
global.flt = (value) => Number.parseFloat(value || 0);
global.cint = (value) => Number.parseInt(value || 0, 10);
global.frappe = {
	pages: { "smartedge-production-planning": {} },
	provide: () => {},
	utils: { escape_html: (value) => String(value) },
};

const {
	capacity_projection_html,
	get_machine_capacity_labels,
	get_utilization_status,
} = require("./smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js");

const configured = get_machine_capacity_labels({
	applicable: 1,
	configured: 1,
	capacity_rate: 35,
	capacity_uom: "Kg/Hour",
	planned_quantity: 350,
	planned_kg: 350,
	quantity_uom: "Kg",
	required_hours: 10,
	rate_configured: 1,
	availability_configured: 1,
	available_capacity: 210,
	workstation_utilization: 75,
	total_allocated_hours: 6,
	net_available_hours: 8,
});
assert.match(configured.html, /35(?:\.00)? Kg\/Hour/);
assert.match(configured.html, /Planned Kg: 350(?:\.000)? Kg/);
assert.match(configured.html, /Planned Hours: 10(?:\.00)? Hours/);
assert.match(configured.html, /Available Capacity: 210(?:\.00)? Kg/);
assert.match(configured.html, /Utilization: 75(?:\.0)?%/);

const missing = get_machine_capacity_labels({
	applicable: 1,
	configured: 0,
	message: "Capacity not configured for this Workstation and production specification.",
});
assert.match(missing.html, /Not Configured/);

const incomplete_context = get_machine_capacity_labels({
	applicable: 1,
	configured: 1,
	rate_configured: 1,
	availability_configured: 0,
	capacity_rate: 35,
	capacity_uom: "Kg/Hour",
	quantity_uom: "Kg",
	required_hours: 2,
	calculation_message: "Capacity not configured for one or more planned operations in this Workstation schedule.",
});
assert.match(incomplete_context.html, /Available Capacity: Not Available/);
assert.match(incomplete_context.html, /Capacity not configured for one or more planned operations/);

assert.equal(get_utilization_status(0), "normal");
assert.equal(get_utilization_status(79.99), "normal");
assert.equal(get_utilization_status(80), "high");
assert.equal(get_utilization_status(100), "high");
assert.equal(get_utilization_status(125), "overloaded");
assert.equal(get_utilization_status(null), "unknown");

const overload = capacity_projection_html({
	workstation: "Extruder-1",
	planned_date: "2026-11-04",
	shift: "Morning",
	available_hours: 8,
	already_planned_hours: 6,
	new_operation_hours: 3,
	projected_total_hours: 9,
	utilization: 112.5,
	overload_hours: 1,
});
assert.match(overload, /Available Capacity<\/th><td>8(?:\.00)? Hours/);
assert.match(overload, /Already Planned<\/th><td>6(?:\.00)? Hours/);
assert.match(overload, /New Operation<\/th><td>3(?:\.00)? Hours/);
assert.match(overload, /Total Planned<\/th><td>9(?:\.00)? Hours/);
assert.match(overload, /Projected Utilization<\/th><td>112\.5%/);
assert.match(overload, /Overload<\/th><td>1(?:\.00)? Hours/);

const unavailable = capacity_projection_html({ available_hours: 0, new_operation_hours: 2, utilization: null });
assert.match(unavailable, /Projected Utilization<\/th><td>N\/A/);
