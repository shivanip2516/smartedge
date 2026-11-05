# QC Pending Visibility and QC Team Notifications

## Existing workflow findings

- Mandatory route checkpoints and SmartEdge Quality Inspection records already exist from Phase 4. The enhancement does not create another QC workflow or operation.
- A QC checkpoint becomes required when the next mandatory route entry is a configured QC operation. Its inspection is linked to the source Production Job and Production Lot.
- The Planning Board previously used the QC lifecycle as the card's main status. This hid the independent production status instead of presenting both.
- Pending inspection creation previously made Quality Manager ToDo assignments, but those assignments did not provide the requested notification subject/content or an explicit missing-recipient warning.

## Visibility behavior

Production status and QC status are now independent. Completed, Running, Planned, Paused, and other production states remain unchanged.

The Planning Board shows a compact second badge on the item card, source operation, and QC operation:

- amber/orange `QC Pending` when the inspection is missing, draft, Pending, Under Review, or awaiting rework inspection;
- green `QC Approved` only after an Accepted decision (or a submitted standard Accepted inspection);
- red `QC Rejected` after a Rejected decision (or a submitted standard Rejected inspection).

The expanded operation details show the checkpoint, source Production Job, operation, workstation, completed quantity, QC status, and Quality Inspection link when the current user has inspection read permission. The SmartEdge Production Job form displays the same separate QC indicator and details without replacing its production-status headline.

## Notification behavior

When a new pending Quality Inspection is created, enabled users holding the existing `Quality Manager` role are resolved dynamically. One Frappe `Notification Log` Alert is inserted per recipient and inspection cycle with:

- subject `QC Inspection Pending — {Production Job} — {Item}`;
- Production Job, finished item, completed operation, workstation, completed quantity, completion time, and Pending status;
- a link to the Quality Inspection.

Idempotency is based on recipient, Alert type, Quality Inspection DocType, and inspection name. Board refreshes, opening a job, and unrelated saves do not invoke notification creation. A reinspection is a new legitimate inspection cycle and may generate a new alert. Existing open Phase 4 ToDo assignments are closed when their inspection receives a decision. Notification Logs remain as history.

If no enabled Quality Manager is configured, production completion is not interrupted. The board and execution QC details display a configuration warning.

## Permissions and scope

QC status is visible to existing planning/execution users. The Quality Inspection reference and inspection-specific employee/time fields are returned only when the current user has Quality Inspection read permission. Existing Quality Manager decision authorization is unchanged.

No production sequence, status transition, quantity, scheduling, employee/shift assignment, workstation, timer, downtime, batch, WIP, stock movement, inventory, or QC decision rule was changed by this enhancement. No schema patch or migration is required.

## Files changed for this enhancement

- `smart_edge_custom/manufacturing/quality_control.py`
- `smart_edge_custom/manufacturing/planning.py`
- `smart_edge_custom/smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.js`
- `smart_edge_custom/smart_edge_custom/page/smartedge_production_planning/smartedge_production_planning.css`
- `smart_edge_custom/smart_edge_custom/doctype/smartedge_production_job/smartedge_production_job.js`
- `smart_edge_custom/test_quality_control.py`
- `smart_edge_custom/test_planning_board_traversal.js`
- `docs/qc_pending_visibility_notifications.md`

## Deployment

After review, deploy the app code, build SmartEdge assets, and clear the site cache. Do not run a migration solely for this enhancement because it adds no DocType or Custom Field changes.
