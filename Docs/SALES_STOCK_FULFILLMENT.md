# Sales stock fulfillment

New sales orders keep the existing automatic receivable, commercial invoice and
finance posting. Fulfillment runs in the same request transaction.

1. Resolve the exact catalog variant for each order line. Color and size take
   precedence over a style's base SKU. The resolved variant ID is retained so
   later catalog renames do not break fulfillment.
2. Reserve only available units (`on_hand - reserved`) across active locations.
   Holds identify the sales order, its line and the stock location. Stock rows
   are locked while allocating, and held units cannot be transferred away.
3. A fully covered order creates one **Pending** shipment with every order line
   and marks the sales order **Packed**, ready for physical dispatch.
   Carrier and destination can be completed in Shipments.
4. An order with a shortfall becomes **PO needed**. **Create PO** appears in both
   the Sales orders list and order detail. It opens a supplier selector. Creation
   uses only missing quantities, the catalog variant's supplier price, and the
   selected supplier. Missing variants or supplier prices produce an actionable
   error before any PO is created. Sales and Procurement write access is required.
5. Expected delivery uses a numeric supplier lead time (days, or weeks converted
   to days); otherwise it defaults to 30 days. The resulting PO can be reviewed in
   Procurement. This PO creates unstarted production/TNA through the existing
   procurement workflow, and the sales order becomes **Awaiting stock**.
6. Available units remain held. Every production item must finish its TNA and
   have its latest non-cancelled final inspection pass.
7. Final QC confirms quality only. Warehouse staff open Inventory > Stock >
   Warehouse receiving, select the location that actually received the goods,
   enter the arrived quantity against each PO line, and record a reference and
   reason. Partial arrivals keep the remaining PO quantities open. Receipts are
   limited to the outstanding quantity and are idempotent by request ID.
   The source sales order receives its manufactured stock first; competing
   waiting orders then reserve remaining stock oldest-first. A fully covered
   order gets one Pending shipment and becomes Packed.
8. In Shipments, **Dispatched** confirms that goods physically left the
   warehouse. It consumes the exact order-owned holds, updates on-hand and
   releases reservations once. In transit, Customs, Out for delivery and
   Delivered require prior dispatch. Delivered records customer receipt and
   never consumes inventory again. Dispatch cannot be undone through a status
   edit. Pending orders can be cancelled before dispatch; dispatched orders
   cannot be cancelled or reopened through the normal order status action.

Repeated Create PO clicks return the linked PO; repeated receipts and shipment
completion do not duplicate procurement, receipts, stock or consumption.
If stock arrives before Create PO is confirmed, availability is checked again
and an unnecessary PO is avoided.

Legacy orders without reconciled, order-owned stock reservations cannot use the
new dispatch action until their stock links are reconciled. V074 preserves old
Delivered shipments, fixes pending orders incorrectly marked Shipped, and
consumes validated holds for existing normal shipments already in transit.

## Catalogue and Inventory restocking

Variant matrices define structure; quantities change through physical receipts
or count corrections. Independent Inventory stock receipts and positive count
corrections recheck tracked orders in PO needed/Awaiting stock. Existing holds
remain protected. Full coverage creates one Pending shipment and marks the
sales order Packed. Failed allocation rolls back the receipt and shipment
creation together. A QC result remains independent of a later receipt failure.

## Catalogue and procurement

Catalogue product rows and product details offer Create PO, prefilled with the
article, its colours and supplier price. The user supplies supplier, quantities
and expected date. Every new procurement PO creates its commercial invoice,
finance payable and production entry in the same transaction. Approval does not
create a second invoice.

Final QC receipt also applies to standalone procurement POs. Their stock enters
inventory and triggers the same waiting-sales-order allocation. Procurement
production cannot ship directly. Exact catalogue variants and an active inventory
location are required before receipt; failures roll back the request.

## Failed inspection recovery

After QC fails, a dialog asks whether to reopen inspection only or production
TNA. The same choice is available on an existing failed inspection. Inspection
only creates a fresh Pending inspection while retaining the TNA. Production
recovery archives the previous stage run for the failed item, creates fresh stages
with the previous durations and workers, and creates a Pending reinspection.
Other production items are unaffected. Starting or passing that inspection is
blocked until the reopened stages finish. Both choices retain the failed record
and link the new inspection into its history. Repeated requests return the same
recovery; changing the chosen recovery afterwards is blocked. Production recovery
requires both Quality and Production write access. Already received stock must
be reconciled before reopening a failure.

Warehouse receipt and dispatch timing require V074 in addition to the earlier migrations.

## Invoice banking and QC details

Sales invoice details show company bank details; commercial sales invoices fill
blank banking fields from the current company profile when opened. Procurement
invoices fill blank fields from the source PO's supplier profile. Saved nonblank
invoice values remain intact, and opening a document does not modify its stored
snapshot. Saving the invoice stores the filled values. Wholesale sales invoices
include IBAN and SWIFT as well as bank name and account number.

In-progress inspections accept either a saved defect type or a typed defect name.
Failed saves keep the entered form values. QC shows the inspected article's
colour/size quantities and reduced size ratio from its source PO, legacy sales
order, or production lines. For example, quantities 40 and 80 show ratio 1:2.
These changes do not require a new database migration.

All PO creation routes, including sales shortage POs, use the same locked invoice
generation helper. Opening Procurement > Invoices with procurement write access
first calls POST /procurement/invoices/generate-missing to repair older POs without
invoices, then loads the list. Existing invoices are left intact. Users with read
access only load the list without generating records. PO creation invalidates the
invoice list cache across Sales, Catalogue, Procurement and Reorder Alerts.

## Migration

Apply V074_pg__physical_warehouse_receipts_and_dispatch.sql after V073 and before
starting the updated backend. Existing Delivered shipments are not relieved
again. Existing in-transit tracked shipments migrate only when their full order
reservations reconcile with physical balances; otherwise migration aborts before
changing stock. Generated Pending shipments correct their source order to Packed.

## Verification

The warehouse suite covers partial arrivals into chosen locations, over-receipt,
QC gating, tenant isolation, idempotent receipts, dispatch from multiple locations,
tracking without double consumption, cancellation boundaries and transaction
rollback. scripts/check_warehouse_postgres.py runs the same checks and a concurrent
receipt retry in a disposable Docker PostgreSQL schema, using the real V074 migration.

## Inventory movement history

V075 adds the physical movement ledger. Inventory > Stock > History shows a
single article; View stock movement history opens the complete ledger. Filters
cover variant, location, type, reference/user search and date range, with pagination.
Every recorded movement retains its article, SKU, colour, size and location names
as they were when posted, its user, quantity, before/after balance and source reference.
Source references open the posted movement group, with links to the source receipt,
shipment or batch where the reader has access.

Receipts, count corrections, both sides of transfers, normal and batch dispatches,
write-offs and supplier returns append entries in the source transaction. Existing
batch returns to QC record a returned quantity with zero warehouse balance change,
because those pieces are held for inspection. Reservations alone do not move physical
stock and therefore do not create physical movement entries.

The migration records an opening balance for every existing variant/location pair.
It preserves earlier recorded entries, but cannot reconstruct missing historical
movements or their old labels. Opening balances identify the start of complete
recording. Posted entries cannot be edited or deleted by the application role;
corrections are separate movements.
