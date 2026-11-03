(() => {
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const link = (doctype, name) => name
		? `<a href="${escape(frappe.utils.get_form_link(doctype, name))}">${escape(name)}</a>` : __("None recorded");
	const links = (doctype, names) => [...new Set(names)].map((name) => link(doctype, name)).join(", ") || __("None recorded");
	frappe.ui.form.on("Batch", {
		async refresh(frm) {
			const request = (frm.operation_trace_request || 0) + 1;
			frm.operation_trace_request = request;
			if (frm.is_new()) return;
			const name = frm.doc.name;
			const trace = frm.doc.custom_batch_purpose === "Operation Traceability";
			const operation = trace || Boolean(frm.doc.custom_operation_lot);
			if (trace) {
				frm.page.set_indicator(__("Completed — Traceability"), "blue");
			}
			frm.dashboard.parent.find(".se-batch-traceability").remove();
			const section = frm.dashboard.add_section(__("Loading verified batch genealogy…"), __("Batch Traceability"), "custom se-batch-traceability");
			frm.dashboard.show();
			try {
				const responses = await Promise.all(["Backward", "Forward"].map((direction) => frappe.call({
					method: "smart_edge_custom.manufacturing.batch_genealogy.get_genealogy",
					args: { batch_no: name, direction },
				})));
				if (frm.operation_trace_request !== request || frm.doc.name !== name) return;
				const [backward, forward] = responses.map((response, index) => {
					const result = response.message || {};
					if (result.operation_batch_trace?.root) return result.operation_batch_trace;
					// Preserve standard manufacturing ancestry for physical material Batches.
					const events = result.root?.events || [];
					return { root: result.root, stock_entries: events.map((event) => event.stock_entry).filter(Boolean),
						[index === 0 ? "parent_batches" : "child_batches"]: events.flatMap((event) => (event.children || []).map((child) => child.batch_no)) };
				});
				const node = backward.root || forward.root || {};
				const rows = [
					[__("Batch"), link("Batch", name)],
					[__("Batch Purpose"), escape(frm.doc.custom_batch_purpose || "Physical Stock")],
					[__("Item"), `${link("Item", frm.doc.item)} — ${escape(frm.doc.item_name)}`],
					[__("Operation"), link("Operation", node.operation || frm.doc.custom_operation_name)],
					[__("Production Job"), link("SmartEdge Production Job", node.production_job || (trace ? frm.doc.reference_name : null))],
					[__("Operation Status"), escape(node.operation_status || (operation ? "Completed" : "Not applicable"))],
					[__("Physical Stock Quantity"), `${escape(frm.doc.batch_qty)} ${escape(node.stock_uom || "")}`],
					[__("Operation Output Quantity"), node.operation_output_quantity != null
						? `${escape(node.operation_output_quantity)} ${escape(node.operation_output_uom)}`
						: (operation ? `${escape(frm.doc.custom_operation_output_quantity)} ${__("Meter")}` : __("Not applicable"))],
					[__("Operation Output UOM"), node.operation_output_uom || (operation ? __("Meter") : __("Not applicable"))],
					[__("Input Material Summary"), node.input_materials?.length
						? node.input_materials.map((row) => `${escape(row.quantity_type)}: ${escape(row.item_name || row.item_code)}${row.batch_no ? ` (${link("Batch", row.batch_no)})` : ""} — ${escape(row.quantity)} ${escape(row.uom)} · ${escape(row.consumption_status)}`).join("<br>")
						: __("No planned or allocated material recorded")],
					[__("Stock Consumption Eligibility"), trace ? __("Not eligible") : __("Standard ERPNext validation")],
					[__("Parent Input Batches"), links("Batch", backward.parent_batches || [])],
					[__("Child Output Batches"), links("Batch", forward.child_batches || [])],
					[__("Final Finished Goods Batches"), links("Batch", [...(backward.final_batches || []), ...(forward.final_batches || [])])],
					[__("Stock Entries"), links("Stock Entry", [...(backward.stock_entries || []), ...(forward.stock_entries || [])])],
				];
				const edges = [...(backward.edges || []), ...(forward.edges || [])];
				const unique = [...new Map(edges.map((edge) => [`${edge.evidence}:${edge.source}:${edge.target}`, edge])).values()];
				section.html(`<table class="table table-bordered"><tbody>${rows.map(([label, value]) => `<tr><th>${escape(label)}</th><td>${value}</td></tr>`).join("")}</tbody></table>
					${unique.map((edge) => `<p>${link("Batch", edge.source)} → ${link("Batch", edge.target)} · ${escape(__(edge.quantity_type || edge.basis))}: ${escape(edge.quantity)} ${escape(edge.uom)} · ${__("Operation output")}: ${escape(edge.operation_output_quantity)} ${escape(edge.operation_output_uom)} · ${escape(edge.source_document_type || "")} ${escape(edge.source_document_id || "")}${edge.source_row_reference ? ` · ${escape(edge.source_row_reference)}` : ""}${edge.source_warehouse ? ` · ${escape(edge.source_warehouse)}` : ""}</p>`).join("")}
					<p class="text-muted">${__("Operation output is traceability metadata. Stock quantities and input quantities retain their recorded units; no batch allocation is inferred.")}</p>
					${[...new Set([...(backward.warnings || []), ...(forward.warnings || [])])].map((warning) => `<p class="text-warning">${escape(warning)}</p>`).join("")}`);
			} catch (error) {
				if (frm.operation_trace_request === request && frm.doc.name === name) {
					section.html(`<p class="text-warning">${__("Unable to load verified genealogy. Check permissions and refresh this Batch to retry.")}</p>`);
				}
			}
		},
	});
})();
