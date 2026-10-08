frappe.pages["smartedge-production-planning"].on_page_load = function (wrapper) {
	wrapper.smartedge_planning = new smart_edge_custom.ProductionPlanningBoard(wrapper);
};

frappe.pages["smartedge-production-planning"].on_page_show = function (wrapper) {
	if (wrapper.smartedge_planning) {
		wrapper.smartedge_planning.refresh();
	}
};

frappe.provide("smart_edge_custom");

smart_edge_custom.ProductionPlanningBoard = class ProductionPlanningBoard {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: "",
			single_column: true,
		});
		this.method = "smart_edge_custom.manufacturing.planning";
		this.state = {
			week_start: this.get_week_start_from_route(),
			board: null,
			filters: {},
		};

		frappe.breadcrumbs.add(__("SmartEdge Manufacturing"));
		this.make_body();
		this.bind_global_events();
	}

	bind_global_events() {
		$(document)
			.off("click.smartedge-production-planning")
			.on("click.smartedge-production-planning", () => {
				if (this.$body) {
					this.$body.find(".se-card-menu.show").removeClass("show");
				}
			});
	}

	make_body() {
		this.$body = $(`<div class="smartedge-planning"></div>`).appendTo(this.page.main);
		this.$header = $(`
			<div class="se-planning-header">
				<div class="se-title-block">
					<div class="se-page-title">${__("Production Planning Board")}</div>
					<div class="se-week-title"></div>
					<div class="se-summary-pills"></div>
				</div>
				<div class="se-header-actions">
					<button class="btn btn-sm btn-default se-prev-week">${__("Previous Week")}</button>
					<button class="btn btn-sm btn-default se-this-week">${__("This Week")}</button>
					<button class="btn btn-sm btn-default se-next-week">${__("Next Week")}</button>
					<button class="btn btn-sm btn-default se-refresh">${__("Refresh")}</button>
					<button class="btn btn-sm btn-primary se-new-production">${__("+ New Production")}</button>
				</div>
			</div>
		`).appendTo(this.$body);
		this.$week = this.$header.find(".se-week-title");
		this.$summary = this.$header.find(".se-summary-pills");
		this.$header.find(".se-prev-week").on("click", () => this.move_week(-7));
		this.$header.find(".se-this-week").on("click", () => this.set_this_week());
		this.$header.find(".se-next-week").on("click", () => this.move_week(7));
		this.$header.find(".se-refresh").on("click", () => this.refresh());
		this.$header.find(".se-new-production").on("click", () => frappe.new_doc("Final EB BOM Generator"));

		this.$filter_row = $(`<div class="se-filter-row"></div>`).appendTo(this.$body);
		this.$board_container = $(`<div class="planning-board-container"></div>`).appendTo(this.$body);
		this.$board_scroll = $(`<div class="smartedge-board-scroll"></div>`).appendTo(this.$board_container);
		this.$board = $(`<div class="smartedge-week-board se-board"></div>`).appendTo(this.$board_scroll);
		this.make_filters();
	}

	make_filters() {
		this.filters = {};
		for (const [fieldname, label] of [
			["category", __("Category")],
			["workstation", __("Workstation")],
			["operation", __("Operation")],
			["status", __("Status")],
			["priority", __("Priority")],
			["finished_product", __("Finished Product")],
			["search", __("Search")],
		]) {
			const fieldtype = fieldname === "finished_product" ? "Link" : fieldname === "search" ? "Data" : "Select";
			const control = frappe.ui.form.make_control({
				parent: this.$filter_row,
				df: {
					fieldname,
					label,
					fieldtype,
					options: fieldname === "finished_product" ? "Item" : [],
					onchange: () => {
						this.state.filters[fieldname] = control.get_value();
						this.refresh();
					},
				},
				render_input: true,
			});
			this.filters[fieldname] = control;
		}
	}

	get_week_start_from_route() {
		const params = new URLSearchParams(window.location.search);
		const week = params.get("week");
		return week || get_monday(frappe.datetime.get_today());
	}

	set_route_week() {
		const route = `/app/smartedge-production-planning?week=${this.state.week_start}`;
		window.history.replaceState(null, "", route);
	}

	set_this_week() {
		this.state.week_start = get_monday(frappe.datetime.get_today());
		this.set_route_week();
		this.refresh();
	}

	move_week(days) {
		this.state.week_start = frappe.datetime.add_days(this.state.week_start, days);
		this.set_route_week();
		this.refresh();
	}

	async refresh() {
		const response = await frappe.call({
			method: `${this.method}.get_weekly_planning_board`,
			args: {
				week_start: this.state.week_start,
				...this.state.filters,
			},
		});
		this.state.board = response.message;
		this.state.week_start = response.message.week_start;
		this.populate_filter_options();
		this.render();
	}

	populate_filter_options() {
		const filters = this.state.board.filters || {};
		this.set_select_options("category", filters.categories || []);
		this.set_select_options("workstation", filters.workstations || []);
		this.set_select_options("operation", filters.operations || []);
		this.set_select_options("status", filters.statuses || []);
		this.set_select_options("priority", filters.priorities || []);
	}

	set_select_options(fieldname, options) {
		const control = this.filters[fieldname];
		if (!control) return;
		const current = control.get_value();
		control.df.options = ["", ...options].join("\n");
		control.refresh();
		if (current) control.set_value(current);
	}

	render() {
		const board = this.state.board;
		this.stop_timers();
		this.$week.text(`${frappe.datetime.str_to_user(board.week_start)} - ${frappe.datetime.str_to_user(board.week_end)}`);
		this.render_summary(board);
		this.$board.empty();
		this.render_pending_column(board.pending || []);
		for (const day of Object.values(board.days || {})) {
			this.render_day_column(day);
		}
		this.start_timers();
	}

	render_summary(board) {
		const cards = [...(board.pending || [])];
		for (const day of Object.values(board.days || {})) {
			cards.push(...(day.operations || []));
		}
		const planned = cards.filter((card) => card.status === "Planned").length;
		const running = cards.filter((card) => card.status === "In Progress").length;
		const completed = cards.filter((card) => card.status === "Completed").length;
		this.$summary.html(`
			<span class="se-summary-pill">${__("Pending")} <strong>${(board.pending || []).length}</strong></span>
			<span class="se-summary-pill">${__("Planned")} <strong>${planned}</strong></span>
			<span class="se-summary-pill">${__("Running")} <strong>${running}</strong></span>
			<span class="se-summary-pill">${__("Done")} <strong>${completed}</strong></span>
		`);
	}

	render_pending_column(cards) {
		const $column = this.make_column(__("Pending for Planning"), "", `${cards.length} ${__("Operations")}`, "pending");
		$column.addClass("pending-column");
		const $body = $column.find(".se-column-body");
		if (!cards.length) {
			$body.append(this.make_empty_state(__("No pending operations")));
		}
		for (const card of cards) {
			$body.append(this.make_card(card));
		}
		this.$board.append($column);
	}

	render_day_column(day) {
		const today = frappe.datetime.get_today();
		const $column = this.make_column(
			day.short_label,
			frappe.datetime.str_to_user(day.date),
			`${day.operation_count} ${__("Operations")}`,
			day.date
		);
		if (day.date === today) {
			$column.addClass("today");
		}
		const $body = $column.find(".se-column-body");
		for (const group of day.groups || []) {
			const group_color = CATEGORY_COLOR_CLASSES[group.color] || "";
			const $group = $(`
				<div class="se-group">
					<div class="se-group-title ${frappe.utils.escape_html(group_color)}">
						<span>${frappe.utils.escape_html(group.category)}</span>
						<strong>${frappe.utils.escape_html(group.count)}</strong>
					</div>
				</div>
			`);
			for (const workstation of group.workstations || []) {
				const capacity = workstation.capacity || {};
				const capacity_label = capacity.configured
					? `${se_format_number(capacity.planned_hours, 1)}h / ${se_format_number(capacity.capacity_hours, 1)}h ${__("planned")}`
					: "";
				const $workstation = $(`
					<div class="se-workstation-group" data-workstation="${frappe.utils.escape_html(workstation.workstation)}">
						<div class="se-workstation" title="${frappe.utils.escape_html(workstation.workstation)}">
							<span>${frappe.utils.escape_html(workstation.workstation)}</span>
							${capacity_label ? `<small>${frappe.utils.escape_html(capacity_label)}</small>` : ""}
						</div>
					</div>
				`);
				this.bind_workstation_drop($workstation, day.date, workstation.workstation);
				for (const card of workstation.cards || []) {
					$workstation.append(this.make_card(card));
				}
				$group.append($workstation);
			}
			$body.append($group);
		}
		if (!day.operation_count) {
			$body.append(this.make_empty_state(__("Drop operation here")));
		}
		this.$board.append($column);
	}

	make_column(title, date, count, date_key) {
		const $column = $(`
			<div class="se-column day-column" data-date="${frappe.utils.escape_html(date_key)}">
				<div class="se-column-header day-header">
					<div>
						<div class="se-column-title">${frappe.utils.escape_html(title)}</div>
						<div class="se-column-date">${frappe.utils.escape_html(date || "")}</div>
					</div>
					<div class="se-column-meta">
						<div class="se-column-count">${frappe.utils.escape_html(count || "")}</div>
					</div>
				</div>
				<div class="se-column-body day-drop-zone"></div>
			</div>
		`);
		if (date_key !== "pending") {
			const $body = $column.find(".se-column-body");
			$body.on("dragover", (event) => {
				event.preventDefault();
				$body.addClass("drag-over");
				$column.addClass("drag-over");
			});
			$body.on("dragleave", () => {
				$body.removeClass("drag-over");
				$column.removeClass("drag-over");
			});
			$body.on("drop", (event) => {
				event.preventDefault();
				$body.removeClass("drag-over");
				$column.removeClass("drag-over");
				const payload = JSON.parse(event.originalEvent.dataTransfer.getData("application/json"));
				this.open_schedule_dialog(payload, date_key);
			});
		}
		return $column;
	}

	bind_workstation_drop($workstation, date_key, workstation) {
		$workstation.on("dragover", (event) => {
			event.preventDefault();
			event.stopPropagation();
			$workstation.addClass("drag-over");
		});
		$workstation.on("dragleave", () => $workstation.removeClass("drag-over"));
		$workstation.on("drop", async (event) => {
			event.preventDefault();
			event.stopPropagation();
			$workstation.removeClass("drag-over");
			const card = JSON.parse(event.originalEvent.dataTransfer.getData("application/json"));
			await this.handle_workstation_drop(card, date_key, workstation);
		});
	}

	async handle_workstation_drop(card, date_key, workstation) {
		if (card.planned_date && card.planned_start) {
			await frappe.call({
				method: `${this.method}.schedule_operation`,
				args: {
					plan_entry: card.id,
					planned_date: date_key,
					workstation,
					planned_start: get_time_from_datetime(card.planned_start) || "09:00",
					duration_hours: card.duration_hours || 1,
					priority: card.priority,
				},
			});
			await this.refresh();
			return;
		}
		await this.open_schedule_dialog(card, date_key, workstation);
	}

	make_card(card) {
		const is_pending = !card.planned_date;
		const qty = `${se_format_number(card.qty, 2)} m`;
		const weight = card.planned_weight_kg ? `${se_format_number(card.planned_weight_kg, 3)} Kg` : "";
		const category_class = CATEGORY_COLOR_CLASSES[card.category_color] || "category-other";
		const status_class = `status-${String(card.status || "Pending").toLowerCase().replace(/\s+/g, "-")}`;
		const product_line = [card.size, card.item_name || card.finished_item].filter(Boolean).join(" • ");
		const qty_line = [qty, weight].filter(Boolean).join(" • ");
		const planned_time = get_planned_time_label(card);
		const tooltip = [
			card.production,
			card.item_name || card.finished_item,
			card.workstation,
			card.operation,
			card.operator_name && `${__("Operator")}: ${card.operator_name}`,
			card.planned_start && `${__("Start")}: ${card.planned_start}`,
			card.duration_hours && `${__("Hours")}: ${se_format_number(card.duration_hours, 2)}`,
		]
			.filter(Boolean)
			.join("\n");
		const $card = $(`
			<div class="se-card plan-card ${category_class} ${is_pending ? "pending-card" : ""}" draggable="true" title="${frappe.utils.escape_html(tooltip)}">
				<div class="se-card-head">
					<span class="se-card-operation-title">${frappe.utils.escape_html(card.operation || "")}</span>
					<span class="se-sequence-badge">#${frappe.utils.escape_html(card.operation_sequence || "")}</span>
				</div>
				<div class="se-card-main">
					<div class="se-card-workstation" title="${frappe.utils.escape_html(card.workstation || "")}">${frappe.utils.escape_html(card.workstation || __("Unassigned Workstation"))}</div>
					<div class="se-product-name" title="${frappe.utils.escape_html(product_line)}">${frappe.utils.escape_html(product_line)}</div>
					<div class="se-card-qty">${frappe.utils.escape_html(qty_line)}</div>
					${this.get_status_line_html(card, planned_time, status_class)}
				</div>
				<div class="se-card-actions">
					${this.get_primary_action_html(card, is_pending, status_class)}
					<div class="dropdown se-card-menu">
						<button class="btn btn-xs btn-default se-icon-btn se-more" data-toggle="dropdown" aria-label="${__("More actions")}" title="${__("More actions")}">⋯</button>
						<ul class="dropdown-menu dropdown-menu-right">
							<li><a class="dropdown-item se-view" href="#">${__("View Details")}</a></li>
							<li><a class="dropdown-item se-edit" href="#">${__("Edit")}</a></li>
							<li><a class="dropdown-item se-plan-all" href="#">${__("Plan Full Route")}</a></li>
							${!is_pending ? `<li><a class="dropdown-item se-unplan" href="#">${__("Unplan")}</a></li>` : ""}
							<li><a class="dropdown-item se-open-production" href="#">${__("Open Production")}</a></li>
						</ul>
					</div>
				</div>
			</div>
		`);
		$card.on("dragstart", (event) => {
			$card.addClass("dragging");
			event.originalEvent.dataTransfer.setData("application/json", JSON.stringify(card));
		});
		$card.on("dragend", () => $card.removeClass("dragging"));
		$card.find(".dropdown-item").on("click", (event) => event.preventDefault());
		$card.find(".se-more").on("click", (event) => {
			event.preventDefault();
			event.stopPropagation();
			const $menu = $(event.currentTarget).closest(".se-card-menu");
			this.$body.find(".se-card-menu.show").not($menu).removeClass("show");
			$menu.toggleClass("show");
		});
		$card.find(".se-card-menu").on("click", (event) => event.stopPropagation());
		$card.find(".se-view").on("click", () => this.open_production_drawer(card.production, card));
		$card.find(".se-edit, .se-plan").on("click", () => this.open_schedule_dialog(card, card.planned_date || this.state.week_start));
		$card.find(".se-plan-all").on("click", () => this.plan_full_production(card));
		$card.find(".se-unplan").on("click", () => this.unplan(card));
		$card.find(".se-open-production").on("click", () => frappe.set_route("Form", "Final EB BOM Generator", card.production));
		$card.find(".se-start").on("click", () => this.open_start_dialog(card));
		$card.find(".se-pause").on("click", () => this.pause_production_job(card));
		$card.find(".se-complete").on("click", () => this.open_complete_dialog(card));
		return $card;
	}

	get_primary_action_html(card, is_pending, status_class) {
		if (is_pending) {
			return `<button class="btn btn-xs btn-primary se-plan">${__("Plan")}</button>`;
		}
		if (card.status === "In Progress") {
			return `
				<button class="btn btn-xs btn-default se-pause">${__("Pause")}</button>
				<button class="btn btn-xs btn-primary se-complete">${__("Complete")}</button>
			`;
		}
		if (card.status === "Completed") {
			return `<span class="se-state-pill ${status_class}">✓</span>`;
		}
		return `<button class="btn btn-xs btn-primary se-start" title="${__("Start Production")}" aria-label="${__("Start Production")}">▶ ${__("Start")}</button>`;
	}

	get_status_line_html(card, planned_time, status_class) {
		if (card.status === "In Progress") {
			const running_label = card.operator_name
				? `${__("RUNNING")} • ${frappe.utils.escape_html(card.operator_name)}`
				: __("RUNNING");
			return `
				<div class="se-running-line ${status_class}">● ${running_label}</div>
				<div class="se-running-timer" data-start="${frappe.utils.escape_html(card.job_actual_start || "")}">00:00:00</div>
			`;
		}
		if (card.status === "Completed") {
			return `<div class="se-card-time">${__("Completed")}${planned_time ? ` • ${frappe.utils.escape_html(planned_time)}` : ""}</div>`;
		}
		if (planned_time) {
			return `<div class="se-card-time">${__("Planned")} ${frappe.utils.escape_html(planned_time)}</div>`;
		}
		return `<div class="se-card-time">${__("Pending workstation schedule")}</div>`;
	}

	make_empty_state(message) {
		return $(`
			<div class="se-empty">
				<span class="se-empty-icon">↧</span>
				<span>${frappe.utils.escape_html(message)}</span>
			</div>
		`);
	}

	async open_schedule_dialog(card, date_key, default_workstation = null) {
		const response = await frappe.call({
			method: `${this.method}.get_compatible_workstations`,
			args: {
				operation: card.operation,
				operation_row: card.operation_row,
			},
		});
		const workstations = response.message || [];
		const default_start = get_time_from_datetime(card.planned_start) || "09:00";
		const default_duration = card.duration_hours || 1;
		const dialog = new frappe.ui.Dialog({
			title: __("Plan Operation"),
			fields: [
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{ fieldtype: "Date", fieldname: "planned_date", label: __("Date"), reqd: 1, default: date_key },
				{
					fieldtype: "Select",
					fieldname: "workstation",
					label: __("Workstation"),
					reqd: 1,
					options: workstations.join("\n"),
					default: default_workstation || card.workstation || workstations[0],
				},
				{ fieldtype: "Time", fieldname: "planned_start", label: __("Planned Start"), reqd: 1, default: default_start },
				{
					fieldtype: "Float",
					fieldname: "duration_hours",
					label: __("Expected Duration (Hours)"),
					reqd: 1,
					default: default_duration,
				},
				{
					fieldtype: "Data",
					fieldname: "planned_end",
					label: __("Planned End"),
					read_only: 1,
				},
				{ fieldtype: "Check", fieldname: "override_sequence", label: __("Override sequence warning") },
				{ fieldtype: "Check", fieldname: "override_capacity", label: __("Override capacity warning") },
			],
			primary_action_label: __("Schedule"),
			primary_action: async (values) => {
				await frappe.call({
						method: `${this.method}.schedule_operation`,
						args: {
							plan_entry: card.id,
							planned_date: values.planned_date,
							workstation: values.workstation,
							planned_start: values.planned_start,
							duration_hours: values.duration_hours,
							priority: card.priority,
							override_sequence: values.override_sequence,
							override_capacity: values.override_capacity,
						},
				});
				dialog.hide();
				await this.refresh();
			},
		});
		dialog.show();
		const update_end = () => {
			const values = dialog.get_values(true) || {};
			dialog.set_value("planned_end", compute_planned_end(values.planned_start, values.duration_hours));
		};
		dialog.fields_dict.planned_start.df.onchange = update_end;
		dialog.fields_dict.duration_hours.df.onchange = update_end;
		dialog.fields_dict.planned_start.$input?.on("change", update_end);
		dialog.fields_dict.duration_hours.$input?.on("change", update_end);
		update_end();
	}

	async unplan(card) {
		frappe.confirm(__("Remove this operation from the schedule?"), async () => {
			await frappe.call({
				method: `${this.method}.unplan_operation`,
				args: {
					plan_entry: card.id,
				},
			});
			await this.refresh();
		});
	}

	async plan_full_production(card) {
		const dialog = new frappe.ui.Dialog({
			title: __("Plan Full Production"),
			fields: [
				{ fieldtype: "Data", fieldname: "production", label: __("Production"), read_only: 1, default: card.production },
				{ fieldtype: "Date", fieldname: "start_date", label: __("Start Date"), reqd: 1, default: card.planned_date || this.state.week_start },
				{ fieldtype: "Time", fieldname: "start_time", label: __("Start Time"), reqd: 1, default: "09:00" },
			],
			primary_action_label: __("Create Plan"),
			primary_action: async (values) => {
				await frappe.call({
					method: `${this.method}.plan_full_production`,
					args: {
						production: card.production,
						start_date: values.start_date,
						start_time: values.start_time,
					},
				});
				dialog.hide();
				await this.refresh();
			},
		});
		dialog.show();
	}

	async open_start_dialog(card) {
		const response = await frappe.call({
			method: `${this.method}.get_start_job_context`,
			args: { plan_entry: card.id },
		});
		const context = response.message || {};
		const dialog = new frappe.ui.Dialog({
			title: __("Start Production"),
			fields: [
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{
					fieldtype: "Data",
					fieldname: "workstation",
					label: __("Workstation"),
					read_only: 1,
					default: context.workstation || card.workstation,
				},
				{
					fieldtype: "Link",
					fieldname: "operator_employee",
					label: __("Employee"),
					options: "Employee",
					reqd: 1,
					default: context.operator_employee,
					get_query: () => {
						const filters = { status: "Active" };
						if (context.company) {
							filters.company = context.company;
						}
						return { filters };
					},
				},
				{
					fieldtype: "Link",
					fieldname: "shift",
					label: __("Shift"),
					options: "Shift Type",
					default: context.shift,
				},
				{
					fieldtype: "Data",
					fieldname: "planned_quantity",
					label: __("Planned Quantity"),
					read_only: 1,
					default: [format_qty(context.planned_qty_meter, "m"), format_qty(context.planned_qty_kg, "Kg")]
						.filter(Boolean)
						.join(" • "),
				},
			],
			primary_action_label: __("Start"),
			primary_action: async (values) => {
				const start_response = await frappe.call({
					method: `${this.method}.start_production_job`,
					args: {
						plan_entry: card.id,
						operator_employee: values.operator_employee,
						shift: values.shift,
					},
				});
				dialog.hide();
				if (start_response.message?.production_job) {
					frappe.set_route("Form", "SmartEdge Production Job", start_response.message.production_job);
				}
			},
		});
		dialog.show();
	}

	async pause_production_job(card) {
		await frappe.call({
			method: `${this.method}.pause_production_job`,
			args: { plan_entry: card.id },
		});
		await this.refresh();
	}

	async open_complete_dialog(card) {
		const dialog = new frappe.ui.Dialog({
			title: __("Complete Production"),
			fields: [
				{ fieldtype: "Data", fieldname: "operation", label: __("Operation"), read_only: 1, default: card.operation },
				{ fieldtype: "Data", fieldname: "workstation", label: __("Workstation"), read_only: 1, default: card.workstation },
				{ fieldtype: "Float", fieldname: "actual_qty_meter", label: __("Actual Meter"), default: card.qty || 0 },
				{ fieldtype: "Float", fieldname: "actual_qty_kg", label: __("Actual Kg"), default: card.planned_weight_kg || 0 },
				{ fieldtype: "Float", fieldname: "good_qty", label: __("Good Qty"), default: card.qty || 0 },
			],
			primary_action_label: __("Complete"),
			primary_action: async (values) => {
				await frappe.call({
					method: `${this.method}.complete_production_job`,
					args: {
						plan_entry: card.id,
						actual_qty_meter: values.actual_qty_meter,
						actual_qty_kg: values.actual_qty_kg,
						good_qty: values.good_qty,
					},
				});
				dialog.hide();
				await this.refresh();
			},
		});
		dialog.show();
	}

	async start_production_job(card) {
		const response = await frappe.call({
			method: `${this.method}.start_production_job`,
			args: {
				plan_entry: card.id,
			},
		});
		if (response.message?.production_job) {
			frappe.set_route("Form", "SmartEdge Production Job", response.message.production_job);
		}
	}

	async open_production_drawer(production, selected_card = null) {
		const response = await frappe.call({
			method: `${this.method}.get_production_plan`,
			args: { production },
		});
		const plan = response.message;
		const rows = (plan.operations || [])
			.map((row) => {
				const mark = row.planned_date ? "Planned" : "Not planned";
				return `<div class="se-drawer-row">
					<strong>${frappe.utils.escape_html(row.sequence || "")}. ${frappe.utils.escape_html(row.operation)}</strong>
					<span>${frappe.utils.escape_html(mark)}${row.planned_date ? ` - ${frappe.datetime.str_to_user(row.planned_date)}` : ""}</span>
				</div>`;
			})
			.join("");
		const dialog = new frappe.ui.Dialog({
			title: plan.production,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "summary",
					options: `
						<div class="se-plan-drawer">
							<p><strong>${frappe.utils.escape_html(plan.item_name || plan.finished_item)}</strong></p>
							<p class="text-muted">${se_format_number(plan.qty, 2)} ${frappe.utils.escape_html(plan.uom || "")} - ${se_format_number(plan.weight, 3)} Kg</p>
							${selected_card ? `
								<div class="se-drawer-focus">
									<div><strong>${__("Operation")}</strong><span>${frappe.utils.escape_html(selected_card.operation || "-")}</span></div>
									<div><strong>${__("Workstation")}</strong><span>${frappe.utils.escape_html(selected_card.workstation || "-")}</span></div>
									<div><strong>${__("Planned")}</strong><span>${frappe.utils.escape_html(get_planned_time_label(selected_card) || "-")}</span></div>
									<div><strong>${__("Employee")}</strong><span>${frappe.utils.escape_html(selected_card.operator_name || "-")}</span></div>
								</div>
							` : ""}
							<p>${__("Compound")}: ${frappe.utils.escape_html(plan.compound || "-")}<br>${__("Base Colour")}: ${frappe.utils.escape_html(plan.base_colour || "-")}<br>${__("Route")}: ${frappe.utils.escape_html(plan.route || "-")}</p>
							<hr>
							${rows}
						</div>
					`,
				},
			],
			primary_action_label: __("Open Production"),
			primary_action: () => frappe.set_route("Form", "Final EB BOM Generator", plan.production),
		});
		dialog.show();
	}

	stop_timers() {
		if (this.timer_interval) {
			clearInterval(this.timer_interval);
			this.timer_interval = null;
		}
	}

	start_timers() {
		const tick = () => {
			this.$body.find(".se-running-timer[data-start]").each((_, element) => {
				const start = $(element).attr("data-start");
				if (!start) return;
				$(element).text(format_elapsed(start));
			});
		};
		tick();
		this.timer_interval = setInterval(tick, 1000);
	}
};

function get_monday(date_value) {
	const date = new Date(`${date_value}T00:00:00`);
	const day = date.getDay();
	const diff = date.getDate() - day + (day === 0 ? -6 : 1);
	date.setDate(diff);
	return date.toISOString().slice(0, 10);
}

function se_format_number(value, precision) {
	return flt(value || 0, precision).toLocaleString(undefined, {
		minimumFractionDigits: 0,
		maximumFractionDigits: precision,
	});
}

function get_time_from_datetime(value) {
	if (!value) return "";
	const parts = String(value).split(" ");
	return (parts[1] || parts[0] || "").slice(0, 5);
}

function get_planned_time_label(card) {
	const start = get_time_from_datetime(card.planned_start);
	const end = get_time_from_datetime(card.planned_end);
	if (start && end) {
		return `${start}–${end}`;
	}
	return start || "";
}

function compute_planned_end(start, duration_hours) {
	if (!start || !duration_hours) return "";
	const parts = String(start).split(":").map((part) => cint(part));
	const date = new Date();
	date.setHours(parts[0] || 0, parts[1] || 0, 0, 0);
	date.setMinutes(date.getMinutes() + flt(duration_hours) * 60);
	return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function format_elapsed(start) {
	const start_time = new Date(String(start).replace(" ", "T"));
	if (Number.isNaN(start_time.getTime())) return "00:00:00";
	const seconds = Math.max(Math.floor((Date.now() - start_time.getTime()) / 1000), 0);
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remaining = seconds % 60;
	return [hours, minutes, remaining].map((value) => String(value).padStart(2, "0")).join(":");
}

function format_qty(value, uom) {
	if (!flt(value)) return "";
	return `${se_format_number(value, 2)} ${uom}`;
}

const CATEGORY_COLOR_CLASSES = {
	blue: "category-extrusion",
	purple: "category-printing",
	indigo: "category-slitting",
	green: "category-qc",
	teal: "category-packing",
	orange: "category-mixing",
	gray: "category-other",
};
