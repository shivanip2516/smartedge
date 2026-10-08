frappe.ui.form.on("SmartEdge Production Job", {
	setup(frm) {
		const employee_query = () => {
			const filters = { status: "Active" };
			if (frm.doc.company) {
				filters.company = frm.doc.company;
			}
			return { filters };
		};
		frm.set_query("operator_employee", employee_query);
		frm.set_query("supervisor_employee", employee_query);
	},

	refresh(frm) {
		set_indicator(frm);
		add_job_actions(frm);
		set_operator_hint(frm);
	},

	operator_employee(frm) {
		set_operator_hint(frm);
	},
});

function set_indicator(frm) {
	if (!frm.doc.status) {
		return;
	}
	const indicator = {
		Draft: "gray",
		"In Progress": "green",
		Paused: "orange",
		Completed: "blue",
		"On Hold": "orange",
		Cancelled: "red",
	}[frm.doc.status] || "gray";
	frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.status), indicator);
}

function add_job_actions(frm) {
	if (frm.is_new() || frm.doc.status === "Completed" || frm.doc.status === "Cancelled") {
		return;
	}
	if (["Draft", "On Hold"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Start Job"), () => call_job_action(frm, "start_job"), __("Actions"));
	}
	if (frm.doc.status === "In Progress") {
		frm.add_custom_button(__("Pause"), () => call_job_action(frm, "pause_job"), __("Actions"));
		frm.add_custom_button(__("Complete Job"), () => call_job_action(frm, "complete_job"), __("Actions"));
	}
	if (frm.doc.status === "Paused") {
		frm.add_custom_button(__("Resume"), () => call_job_action(frm, "resume_job"), __("Actions"));
		frm.add_custom_button(__("Complete Job"), () => call_job_action(frm, "complete_job"), __("Actions"));
	}
}

async function call_job_action(frm, method) {
	await frm.call(method);
	await frm.reload_doc();
}

function set_operator_hint(frm) {
	if (frm.doc.status === "Draft" && !frm.doc.operator_employee) {
		frm.set_intro(__("Select an active Employee as Operator before starting this production job."), "orange");
		return;
	}
	frm.set_intro("");
}
