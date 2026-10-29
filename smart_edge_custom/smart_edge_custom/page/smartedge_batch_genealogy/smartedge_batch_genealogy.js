frappe.pages["smartedge-batch-genealogy"].on_page_load = function (wrapper) {
	new SmartEdgeBatchGenealogy(wrapper);
};

class SmartEdgeBatchGenealogy {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("SmartEdge Batch Genealogy"),
			single_column: true,
		});
		this.make_filters();
		this.$root = $('<div class="smartedge-batch-genealogy"></div>').appendTo(this.page.main);
		this.bind_links();
		this.render_empty(__("Select a batch to trace its actual manufacturing history."));
	}

	make_filters() {
		this.reference_type = this.page.add_field({
			label: __("Trace Reference"),
			fieldname: "reference_type",
			fieldtype: "Select",
			options: ["ERPNext Batch", "Production Lot", "Production Job", "Final EB BOM Generator"],
			default: "ERPNext Batch",
		});
		this.item = this.page.add_field({
			label: __("Item"),
			fieldname: "item_code",
			fieldtype: "Link",
			options: "Item",
			change: () => {
				if (this.batch.get_value()) this.batch.set_value("");
			},
		});
		this.batch = this.page.add_field({
			label: __("Batch Number"),
			fieldname: "batch_no",
			fieldtype: "Link",
			options: "Batch",
			get_query: () => ({ filters: this.item.get_value() ? { item: this.item.get_value() } : {} }),
		});
		this.production_lot = this.page.add_field({ label: __("Production Lot"), fieldname: "production_lot", fieldtype: "Link", options: "SmartEdge Production Lot" });
		this.final_eb_bom_generator = this.page.add_field({ label: __("Final EB Production Reference"), fieldname: "final_eb_bom_generator", fieldtype: "Link", options: "Final EB BOM Generator" });
		this.direction = this.page.add_field({
			label: __("Trace Direction"),
			fieldname: "direction",
			fieldtype: "Select",
			options: ["Backward", "Forward"],
			default: "Backward",
		});
		this.stage = this.page.add_field({
			label: __("Stage"),
			fieldname: "manufacturing_stage",
			fieldtype: "Data",
		});
		this.from_date = this.page.add_field({ label: __("From Date"), fieldname: "from_date", fieldtype: "Date" });
		this.to_date = this.page.add_field({ label: __("To Date"), fieldname: "to_date", fieldtype: "Date" });
		this.work_order = this.page.add_field({ label: __("Work Order"), fieldname: "work_order", fieldtype: "Link", options: "Work Order" });
		this.production_job = this.page.add_field({ label: __("Production Job"), fieldname: "production_job", fieldtype: "Link", options: "SmartEdge Production Job" });
		this.company = this.page.add_field({ label: __("Company"), fieldname: "company", fieldtype: "Link", options: "Company" });
		this.page.set_primary_action(__("Trace Genealogy"), () => this.trace(), "search");
		if ((frappe.user_roles || []).some((role) => ["System Manager", "Manufacturing Manager"].includes(role))) {
			this.page.add_menu_item(__("Rebuild Genealogy"), () => this.rebuild());
		}
	}

	async trace() {
		const reference_type = this.reference_type.get_value() || "ERPNext Batch";
		const batch_no = this.batch.get_value();
		const references = {
			"ERPNext Batch": batch_no,
			"Production Lot": this.production_lot.get_value(),
			"Production Job": this.production_job.get_value(),
			"Final EB BOM Generator": this.final_eb_bom_generator.get_value(),
		};
		const reference = references[reference_type];
		if (!reference) {
			frappe.msgprint(__("Select the {0} to trace.", [reference_type]));
			return;
		}
		this.render_loading();
		try {
			if (reference_type !== "ERPNext Batch") {
				const response = await frappe.call({
					method: "smart_edge_custom.manufacturing.production_lot.get_production_lot_genealogy",
					args: {
						reference,
						reference_type,
						direction: this.direction.get_value() || "Backward",
					},
				});
				this.render_lot_result(response.message || {});
				return;
			}
			const response = await frappe.call({
				method: "smart_edge_custom.manufacturing.batch_genealogy.get_genealogy",
				args: {
					batch_no,
					direction: this.direction.get_value() || "Backward",
					filters: {
						manufacturing_stage: this.stage.get_value(),
						from_date: this.from_date.get_value(),
						to_date: this.to_date.get_value(),
						work_order: this.work_order.get_value(),
						production_job: this.production_job.get_value(),
						company: this.company.get_value(),
					},
				},
			});
			this.render_result(response.message || {});
		} catch (error) {
			this.render_empty(__("Unable to load batch genealogy."));
			throw error;
		}
	}

	render_lot_result(result) {
		if (!(result.roots || []).length) {
			this.render_empty(__("No recorded production-lot genealogy was found."));
			return;
		}
		const warning = result.truncated ? `<div class="se-genealogy-warning">${__("The result reached the safe traversal limit.")}</div>` : "";
		this.$root.html(`
			<div class="se-genealogy-summary">
				<strong>${this.escape(result.direction || "")}</strong>
				<span>${__("{0} SmartEdge production lots (non-stock)", [result.lot_count || 1])}</span>
			</div>
			${warning}
			<div class="se-genealogy-tree">${result.roots.map((node) => this.render_lot(node, 0)).join("")}</div>
		`);
	}

	render_lot(node, depth) {
		const movement_rows = (node.movements || []).map((row) => `
			<div class="se-genealogy-event">
				<div class="se-event-head"><span class="se-stage-label">${this.escape(row.movement_type)}</span><span>${this.escape(row.source_operation || "")} → ${this.escape(row.destination_operation || "")}</span><time>${this.escape(row.posting_datetime || "")}</time></div>
				<div class="se-event-refs">${row.production_job ? this.link("SmartEdge Production Job", row.production_job, row.production_job) : ""}${row.reference_name ? ` · ${this.link(row.reference_doctype, row.reference_name, row.reference_name)}` : ""}</div>
				<div class="se-event-grid">
					<div><label>${__("Source")}</label><span>${row.source_production_lot ? this.link("SmartEdge Production Lot", row.source_production_lot, row.source_production_lot) : (row.source_stock_batch ? this.link("Batch", row.source_stock_batch, row.source_stock_batch) : __("Production output"))}</span></div>
					<div><label>${__("Destination")}</label><span>${row.destination_production_lot ? this.link("SmartEdge Production Lot", row.destination_production_lot, row.destination_production_lot) : (row.destination_stock_batch ? this.link("Batch", row.destination_stock_batch, row.destination_stock_batch) : __("Finished Goods"))}</span></div>
					<div><label>${__("Actual Quantity")}</label><span>${this.number(row.quantity)} ${this.escape(row.uom || "")}${row.quantity_kg ? ` / ${this.number(row.quantity_kg)} Kg` : ""}</span></div>
				</div>
			</div>`).join("");
		const batches = (node.stock_batches || []).map((name) => this.link("Batch", name, name)).join(", ") || __("None recorded");
		return `
			<details class="se-batch-node" ${depth < 2 ? "open" : ""}>
				<summary><span class="se-node-marker"></span><span class="se-node-title">${this.link("SmartEdge Production Lot", node.production_lot, node.production_lot)}</span><span class="se-node-item">${this.link("Item", node.finished_item, node.finished_item)}</span>${node.cycle ? `<span class="se-genealogy-flag">${__("Cycle protected")}</span>` : ""}</summary>
				<div class="se-batch-meta"><strong>${__("SmartEdge Production Lot — non-stock traceability")}</strong> · ${__("Operation")}: ${this.escape(node.operation || "")} · ${__("Produced")}: ${this.number(node.produced_quantity)} ${this.escape(node.quantity_uom || "")} · ${__("Available")}: ${this.number(node.available_quantity)} · ${__("Transferred")}: ${this.number(node.transferred_quantity)} · ${__("Remaining at stage")}: ${this.number(node.remaining_quantity)} · ${__("ERPNext input batches")}: ${batches}</div>
				<div class="se-event-list">${movement_rows || `<div class="se-genealogy-leaf">${__("No movements recorded")}</div>`}<div class="se-event-children">${(node.children || []).map((child) => this.render_lot(child, depth + 1)).join("")}</div></div>
			</details>`;
	}

	async rebuild() {
		const dialog = new frappe.ui.Dialog({
			title: __("Rebuild Batch Genealogy"),
			fields: [
				{ fieldname: "batch_no", label: __("Batch Number"), fieldtype: "Link", options: "Batch" },
				{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
				{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
				{ fieldname: "limit", label: __("Maximum Stock Entries"), fieldtype: "Int", default: 500 },
			],
			primary_action_label: __("Rebuild"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					const response = await frappe.call({
						method: "smart_edge_custom.manufacturing.batch_genealogy.rebuild_genealogy",
						args: values,
						freeze: true,
						freeze_message: __("Rebuilding verifiable genealogy links…"),
					});
					const result = response.message || {};
					frappe.msgprint(__("Processed {0} Stock Entries; created {1}, updated {2}, skipped {3} movements.", [result.processed || 0, result.created || 0, result.updated || 0, result.skipped || 0]));
					dialog.hide();
				} finally {
					dialog.enable_primary_action();
				}
			},
		});
		dialog.show();
	}

	render_loading() {
		this.$root.html(`<div class="se-genealogy-empty">${__("Loading actual batch movements…")}</div>`);
	}

	render_empty(message) {
		this.$root.html(`<div class="se-genealogy-empty">${frappe.utils.escape_html(message)}</div>`);
	}

	render_result(result) {
		if (!result.root) {
			this.render_empty(__("No verifiable genealogy was found for this batch."));
			return;
		}
		const warning = result.truncated
			? `<div class="se-genealogy-warning">${__("The result reached the safe traversal limit. Narrow the filters to continue.")}</div>`
			: "";
		this.$root.html(`
			<div class="se-genealogy-summary">
				<strong>${frappe.utils.escape_html(result.direction || "")}</strong>
				<span>${__("{0} batches", [result.batch_count || 1])}</span>
			</div>
			${warning}
			<div class="se-genealogy-tree">${this.render_batch(result.root, 0)}</div>
			${(result.production_lot_roots || []).length ? `
				<div class="se-genealogy-summary"><strong>${__("Downstream operation lots")}</strong><span>${__("{0} SmartEdge production lots (non-stock)", [result.production_lot_count || result.production_lot_roots.length])}</span></div>
				<div class="se-genealogy-tree">${result.production_lot_roots.map((node) => `<div class="se-batch-meta">${__("Linked from ERPNext batch")}: ${this.link("Batch", node.attached_to_batch, node.attached_to_batch)}</div>${this.render_lot(node, 0)}`).join("")}</div>
			` : ""}
		`);
	}

	render_batch(node, depth) {
		const events = node.events || [];
		const batch_link = this.link("Batch", node.batch_no, node.batch_no);
		const item_link = this.link("Item", node.item_code, node.item_code);
		const flags = node.cycle ? `<span class="se-genealogy-flag">${__("Cycle protected")}</span>` : "";
		return `
			<details class="se-batch-node" ${depth < 2 ? "open" : ""}>
				<summary>
					<span class="se-node-marker"></span>
					<span class="se-node-title">${batch_link}</span>
					<span class="se-node-item">${item_link}</span>
					${flags}
				</summary>
				<div class="se-batch-meta">
					${node.manufacturing_date ? `${__("Manufactured")}: ${this.escape(node.manufacturing_date)}` : ""}
					${node.batch_quantity != null ? ` · ${__("Current batch quantity")}: ${this.number(node.batch_quantity)}` : ""}
					${node.manufacturing_stage ? ` · ${__("Stage")}: ${this.escape(node.manufacturing_stage)}` : ""}
					${node.production_quantity != null ? ` · ${__("Produced")}: ${this.number(node.production_quantity)} ${this.escape(node.production_uom || "")}` : ""}
					${node.warehouse ? ` · ${__("Warehouse")}: ${this.link("Warehouse", node.warehouse, node.warehouse)}` : ""}
					${node.batch_status ? ` · ${__("Status")}: ${this.escape(node.batch_status)}` : ""}
				</div>
				<div class="se-event-list">
					${events.length ? events.map((event) => this.render_event(event, depth)).join("") : `<div class="se-genealogy-leaf">${__("No further verified manufacturing event")}</div>`}
				</div>
			</details>
		`;
	}

	render_event(event, depth) {
		const refs = [
			event.stock_entry ? this.link("Stock Entry", event.stock_entry, event.stock_entry) : "",
			event.work_order ? this.link("Work Order", event.work_order, event.work_order) : "",
			event.production_job ? this.link("SmartEdge Production Job", event.production_job, event.production_job) : "",
		].filter(Boolean).join(" · ");
		const quantities = (rows) => this.render_batch_quantities(rows);
		const quality_inspections = (event.quality_inspections || [])
			.map((name) => this.link("Quality Inspection", name, name))
			.join(", ") || __("Not Recorded");
		return `
			<div class="se-genealogy-event">
				<div class="se-event-head">
					<span class="se-stage-label">${this.escape(event.stage || __("Manufacturing"))}</span>
					<span>${this.escape(event.operation || "")}</span>
					<time>${this.escape(event.posting_datetime || "")}</time>
				</div>
				<div class="se-event-refs">${refs}</div>
				<div class="se-event-grid">
					<div><label>${__("Actual Inputs")}</label><span>${quantities(event.inputs)}</span></div>
					<div><label>${__("Actual Outputs")}</label><span>${quantities(event.outputs)}</span></div>
					<div><label>${__("Workstation / Employee")}</label><span>${this.escape(event.workstation || __("Not Recorded"))} · ${event.employee ? this.link("Employee", event.employee, event.employee) : __("Not Recorded")}</span></div>
					<div><label>${__("Scrap / Recovery")}</label><span>${quantities(event.scrap_recovery)} · ${__("Recorded usable")}: ${this.number(event.usable_scrap)} · ${__("unusable")}: ${this.number(event.unusable_scrap)}</span></div>
					<div><label>${__("Quality")}</label><span>${__("Good")}: ${this.number(event.good_quantity)} · ${__("Rejected")}: ${this.number(event.rejected_quantity)} · ${__("Rework")}: ${this.number(event.rework_quantity)}</span></div>
					<div><label>${__("QC Reference")}</label><span>${quality_inspections}</span></div>
					<div><label>${__("Allocation")}</label><span>${__("Event-level evidence; no inferred batch allocation")}</span></div>
				</div>
				${this.render_operation_context(event.operation_context || [])}
				<div class="se-event-children">${(event.children || []).map((child) => this.render_batch(child, depth + 1)).join("")}</div>
			</div>
		`;
	}

	render_operation_context(rows) {
		if (!rows.length) return "";
		return `
			<details class="se-operation-context">
				<summary>${__("Operation execution history")} (${rows.length})</summary>
				<div>
					${rows.map((row) => `
						<div class="se-operation-context-row">
							<strong>${this.escape(row.operation || __("Operation"))}</strong>
							<span>${this.link("SmartEdge Production Job", row.production_job, row.production_job)}</span>
							<span>${this.escape(row.workstation || __("No workstation"))} · ${row.employee ? this.link("Employee", row.employee, row.employee) : __("No employee")}</span>
							<span>${this.escape(row.actual_start || "")} → ${this.escape(row.actual_end || "")}</span>
							<span>${__("Actual")}: ${this.render_operation_quantity(row)} · ${__("Good")}: ${this.number(row.good_quantity)} · ${__("Rejected")}: ${this.number(row.rejected_quantity)} · ${__("Rework")}: ${this.number(row.rework_quantity)} · ${__("Scrap")}: ${this.number((row.usable_scrap || 0) + (row.unusable_scrap || 0))}</span>
							<span><strong>${this.escape(row.tracking_mode || __("Operation History"))}</strong></span>
							${row.evidence_basis ? `<span>${__("Evidence")}: ${this.escape(row.evidence_basis)}</span>` : ""}
							${(row.input_batches || []).length ? `<span>${__("Actual Input Batches")}: ${this.render_batch_quantities(row.input_batches)}</span>` : ""}
							${(row.output_batches || []).length ? `<span>${__("Actual Output Batches")}: ${this.render_batch_quantities(row.output_batches)}</span>` : ""}
							${row.related_stock_entry ? `<span>${__("Stock Entry")}: ${this.link("Stock Entry", row.related_stock_entry, row.related_stock_entry)}</span>` : ""}
						</div>
					`).join("")}
				</div>
			</details>
		`;
	}

	render_batch_quantities(rows) {
		return (rows || [])
			.map((row) => `${this.link("Batch", row.batch_no, row.batch_no)} ${this.number(row.quantity)} ${this.escape(row.uom || "")}`)
			.join(", ") || __("Not Recorded");
	}

	render_operation_quantity(row) {
		if (row.plan_type === "Required Material") {
			return `${this.number(row.actual_qty_meter)} ${__("batches")} / ${this.number(row.actual_qty_kg)} ${this.escape(row.quantity_uom || "")}`;
		}
		return `${this.number(row.actual_qty_meter)} m / ${this.number(row.actual_qty_kg)} Kg`;
	}

	bind_links() {
		this.$root.on("click", "[data-doctype][data-name]", (event) => {
			event.preventDefault();
			event.stopPropagation();
			const $link = $(event.currentTarget);
			frappe.set_route("Form", $link.attr("data-doctype"), $link.attr("data-name"));
		});
	}

	link(doctype, name, label) {
		if (!name) return "";
		return `<a href="#" data-doctype="${this.escape(doctype)}" data-name="${this.escape(name)}">${this.escape(label || name)}</a>`;
	}

	escape(value) {
		return frappe.utils.escape_html(String(value == null ? "" : value));
	}

	number(value) {
		return format_number(value || 0, null, 6);
	}
}
