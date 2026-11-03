const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
let handlers;
let previews = 0;
global.frappe = {
	ui: { form: { on: (doctype, events) => { handlers = events; } } },
	utils: { debounce: (callback) => callback },
};
global.__ = (value) => value;
global.cint = (value) => Number(value) || 0;
global.flt = global.cint;
vm.runInThisContext(fs.readFileSync(require.resolve("./smart_edge_custom/doctype/final_eb_bom_generator/final_eb_bom_generator.js"), "utf8"));
const queries = {};
const frm = {
	doc: { compound_item: "ITEM-COMPOUND", base_colour_item: "ITEM-SHADE" },
	set_query: (field, callback) => { queries[field] = callback; },
};
handlers.setup(frm);
assert.deepEqual(queries.compound_item(), { filters: { item_group: "Compounds" } });
assert.deepEqual(queries.base_colour_item(), { filters: { item_group: "Base Colour" } });
assert.equal(handlers.compound, undefined);
assert.equal(handlers.base_colour, undefined);
frm.preview_debounced = () => previews++;
handlers.compound_item(frm);
handlers.base_colour_item(frm);
assert.equal(previews, 2);
handlers.printing_route(frm);
assert.equal(previews, 3);
frm.__applying_preview = true;
handlers.compound_item(frm);
handlers.base_colour_item(frm);
assert.equal(previews, 3);
frm.__applying_preview = false;
frm.set_value = (field, value) => { frm.doc[field] = value; };
frm.fields_dict = {};
frm.refresh_field = () => {};
frm.clear_table = () => {};
// A preview may carry derived fields, but it must not replace authoritative selections.
apply_preview(frm, { compound_item: "OTHER-COMPOUND", base_colour_item: "OTHER-SHADE" });
assert.equal(frm.doc.compound_item, "ITEM-COMPOUND");
assert.equal(frm.doc.base_colour_item, "ITEM-SHADE");
console.log("Generator client checks passed: Item queries, selector events, preview guards and authoritative selections.");
