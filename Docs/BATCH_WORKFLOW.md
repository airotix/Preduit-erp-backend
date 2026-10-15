# Shared batches across Production, Quality and Shipments

## Structure

One purchase order has one main production order and several independently tracked production suborders (batches). A batch remains a subdivision of the PO, not a new purchasing commitment. The Production Orders list shows only the main order; its detail shows the batch suborders beneath it. Each batch style has the same independent production timeline and Tech Pack gate as a normal order style. The public Batch ID is shared by every module. Inspection submissions have their own real Quality inspection IDs; shipment records have their own shipment IDs and retain their source Batch IDs.

## Daily workflow

1. **Production → Orders → main order → batch suborder:** plan quantities per article, colour and size, then open each batch suborder. Start each style independently with the normal Tech Pack, Trims, Lining, Cutting, Sewing, Finishing and Packed timeline. Stage start, completion, assignment, notes, extensions and resolve actions remain in Production. Record produced quantities for a style after its timeline is complete. The main order displays aggregate production progress; it does not run another writable timeline for quantities assigned to batches. No production action approves QC or posts inventory.
2. **Quality → Inspections / Batch QC work:** open the same Batch ID in Quality. Inline QC can be recorded during production without releasing pieces. Final QC requires that style's completed TNA and records assessed quantity, sample size, approved, held/rework and rejected/scrap quantities. Reinspection redistributes held pieces. Every submission creates a linked Quality inspection record with its inspector, quantities, findings, time and inspection ID. Batch inspection history stays in Quality; the existing legacy inspection workflow remains available for legacy orders.
3. **Shipments → Batch shipment tracking:** select approved quantities whose TNA is complete. Create an inbound factory-to-warehouse shipment and select its receiving warehouse. Several batches can share a shipment, and one batch can have partial shipments. Pending and in-transit allocations prevent the same pieces being promised twice. Factory dispatch and transit do not add inventory.
4. **Inventory → Stock Levels / Batch warehouse receiving:** confirm physical delivery and accepted quantities. Warehouse permission is required for this action; it is also available in the shipment detail for warehouse-authorized users. Marking this inbound shipment Delivered posts only accepted quantities, creates the goods receipt and retains batch/variant/location provenance. A retry cannot post the receipt twice. Short or unaccepted pieces go to hold for reconciliation and are not silently available for reshipment. Partial deliveries are tracked as separate shipments. The PO becomes Partially received, then Received when its ordered quantity is received across batches.
5. **Outbound customer shipment:** after warehouse receipt, shipping staff can allocate available batch stock, optionally with older inventory reserved for the sales order. Warehouse dispatch consumes stock. Customer delivery tracks delivery only and never increases inventory. Full batch-only customer returns continue to enter QC hold; partial or mixed-origin returns require the existing returns reconciliation process.

Production, Quality, Shipments and warehouse receiving have separate screens and write actions. Other departments' status is available through direct links, while each work screen shows its own actions and counters. PO and sales order screens show consolidated read-only batch progress. Batch lists keep completed work visible. Writes refresh related views; open batch views refresh every 30 seconds, and navigation/focus reloads current data.

## Quantity controls

- Final QC dispositions must total the assessed lot; sample size is separate.
- Inbound eligibility is approved minus received and pending/in-transit inbound allocations, and requires completed TNA.
- Held, rejected and unassessed pieces cannot be allocated inbound.
- A batch of 500 with 300 approved and 200 held can send only the 300 approved pieces while the remainder stays in QC.
- Delivered inbound quantities and outbound/customer quantities are separate counters.
- Batch quantity planning, QC, allocation and receipts serialize against the parent order. Command request IDs protect retries, and every batch ledger is tenant-scoped.
- Pending inbound shipments can be cancelled to release their quantities. Dispatched inbound shipments require reconciliation rather than silent cancellation. Inactive documents cannot continue execution.

## Existing data and deployment

Apply V069, V070 and V071 in order and deploy the frontend and backend together. V069 adds TNA scheduling fields and the inbound allocation ledger. V070 gives each style in existing batches its own copy of the recorded TNA status and dates. V071 links batch QC history to real Quality inspections without changing quantity ledgers. Existing shipments remain Outbound. Existing received stock, batch IDs and audit history are retained; the migrations do not replay receipts or reinterpret old shipments as inbound.

Older unfinished batch stages without dates can be scheduled in Production before further TNA progress. Planning the first unfinished stage schedules subsequent unfinished stages for that style; completed stage history is retained. Existing legacy whole-order TNA remains available until an order is batch-managed; each batch style has one authoritative timeline.

V069, V070 and V071 are applied to the local Docker database. V070 and V071 were also verified in a disposable PostgreSQL schema. Other environments use the normal incremental migration runner; production deployment is separate.
