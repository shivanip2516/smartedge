const assert = require("node:assert/strict");
const vm = require("node:vm");
const context = vm.createContext({
	__: (value) => value,
	frappe: { listview_settings: {}, utils: { escape_html: (value) => String(value) } },
});
vm.runInContext(require("node:fs").readFileSync(
	"smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator_list.js", "utf8"), context);
const settings = context.frappe.listview_settings["Final EB BOM Generator"];
const doc = { status: "Completed", production_status: "Completed" };
assert.equal(settings.formatters.status(doc.status, null, doc), "Completed");
assert.deepEqual(Array.from(settings.get_indicator(doc)), ["Completed", "green", "status,=,Completed"]);
console.log("Final EB BOM Generator list uses production lifecycle status");
