# SmartEdge Batch Genealogy Architecture

## Source of truth

Genealogy is derived only from submitted `Manufacture` Stock Entries. Actual batch quantities come from each Stock Entry Detail's submitted Serial and Batch Bundle (or its legacy `batch_no` field). Quantities are stored in the row's stock UOM, so existing Gram/Kg conversion and FIFO selection remain untouched.

Planning records, BOM quantities, scheduled operations, and FIFO predictions are never treated as actual consumption.

## Data model

`SmartEdge Batch Genealogy Movement` stores one independently evidenced batch movement:

- Input: a batch actually consumed by a manufacturing Stock Entry.
- Output: a finished batch actually received by that Stock Entry.
- Scrap / Recovery: a separately batched non-finished receipt recorded by that Stock Entry.

All movements from the same Stock Entry form one manufacturing event. This supports many inputs, many outputs, splitting, merging, and partial production without inventing an exact input-to-output allocation. The UI labels allocation as event-level evidence.

The stable source key is a SHA-256 digest of Stock Entry, Stock Entry Detail, batch, and direction. Reprocessing updates the same movement instead of inserting a duplicate.

## Manufacturing context

Where available, each event records its Work Order, Production Plan Entry, Production Job, operation, workstation, employee, actual times, QC reference, and the existing job quality/scrap quantities. For finished EB manufacture events, completed route jobs are retained as operation-level execution context. Operations without their own physical output batch are shown in that context and are not represented as fictional stock movement.

For final manufacturing events, actual prepared-material inputs are associated with the first configured Extrusion route job and the actual output batch, when one exists, is associated with the job that posted final output. Intermediate Printing, Slitting, QC, and Packing jobs retain their execution history and are explicitly labelled as continuing the same physical identity unless a submitted Stock Entry proves a separate WIP batch transition. A separately submitted operation Stock Entry attaches its actual input and output batches directly to that operation.

Batch names remain controlled by each Item's standard ERPNext `batch_number_series`. Genealogy does not rename existing batches, override supplier batches, enable batch control, or create WIP/finished batches solely for reporting.

## Lifecycle

- Stock Entry `on_submit`: capture or update active movements.
- Production Job `on_update`: refresh execution context after the job receives its Stock Entry reference.
- Final EB Stock Entry linking: refresh the event after final jobs are linked.
- Stock Entry `on_cancel`: retain rows for audit but mark them Cancelled. Active traversal excludes them.
- Amendment submission: creates or updates movements under the amended Stock Entry's own stable source keys.

No genealogy hook changes inventory, Stock Entries, batches, Work Orders, FIFO selection, UOM conversion, or production quantities.

## Traversal and permissions

Backward traversal finds events where the selected batch is an output, then follows all actual input batches. Forward traversal finds events where it is an input, then follows all actual output batches. Queries are batched by level, limited to 12 levels and 250 batch nodes, and protected against cycles.

Movement queries honor DocType and Company user permissions. Every Stock Entry and linked Batch, Item, Work Order, Production Job, operation, workstation, employee, and Quality Inspection reference is checked for read permission before it is returned.

## Historical rebuild

System Managers and Manufacturing Managers can run a controlled rebuild from the Batch Genealogy page by batch or date range, with a maximum of 500 Stock Entries per request. Unsupported/unbatched movements are skipped and logged; historical stock documents are never edited.
