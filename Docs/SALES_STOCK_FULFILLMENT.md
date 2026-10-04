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
   and immediately marks the sales order **Shipped** (handed to Shipments).
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
7. Final QC automatically creates a completed goods receipt and adds the PO's
   full quantities to inventory once. The same action runs from Complete
   inspection, a Pass status update, and Pass inspection. Finishing the last TNA
   stage also checks the gate in case QC was passed before production finished.
   The original order receives its manufactured units first; other waiting
   orders are then checked oldest-first and reserve only remaining available
   stock. Each fully covered order gets one Pending shipment. Shipment readiness
   is based on its actual inventory holds, including stock from other receipts.
   An active inventory location is required; a missing location stops the action
   with a clear error. Receipt and QC commit together. Manual receipt after an
   automatic receipt returns the existing receipt without adding stock again.
8. Delivering the shipment consumes the exact
   held stock and releases reservations once. Holds remain protected while the
   shipment is Pending or in transit, even though the sales order is Shipped.
   Cancellation before shipment creation releases only that
   order's holds. Terminal orders cannot be reopened or shipped again.

Repeated Create PO clicks return the linked PO; repeated receipts and shipment
completion do not duplicate procurement, receipts, stock or consumption.
If stock arrives before Create PO is confirmed, availability is checked again
and an unnecessary PO is avoided.

Existing orders are not retroactively allocated or relinked. Their
`fulfillment_tracked` flag defaults to false, retaining legacy behavior.

## Catalogue and Inventory restocking

Positive quantity increases saved through either variant matrix, and manual
Inventory stock receipts, now recheck tracked orders in PO needed/Awaiting stock.
The waiting queue is locked before stock writes and processed oldest-first in the
same transaction. Existing holds remain protected; extra available units are held
until every order line is covered. Full coverage creates one Pending shipment and
marks the sales order Shipped. Partial restocks keep the appropriate waiting status.
Repeated matrix saves do not create duplicate holds or shipments. Failed allocation
rolls back the stock increase and shipment together.

Independent stock can fulfill an order that already has a production PO. That PO
is retained; its manufactured quantities still require completed TNA and final QC
before inventory receipt. Stock reductions and colour-only/no-quantity edits do
not trigger reallocation. Legacy, cancelled, shipped and other-tenant orders are
excluded. The frontend refreshes Sales and Shipments after these stock writes.
This extension needs no new database migration.

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

These extensions use existing fields and require no migration beyond V063/V064.

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

Apply V063 first if it is not already installed, then V064 before restarting the
backend with these changes. V064 is also included in the consolidated PostgreSQL
schema for fresh databases. Do not run the consolidated schema as an upgrade.

From any PowerShell directory, for the existing Docker database:

```powershell
Get-Content -Raw "C:\Users\fbc\Desktop\perp\backend\db\V064_pg__sales_stock_fulfillment.sql" |
  docker exec -i preduit-db psql -U erp_admin -d preduit -v ON_ERROR_STOP=1
```

The migration adds source links, the tracked-order flag, stable variant IDs,
order-owned reservations with tenant RLS and grants, and a unique active shipment
per sales order. It leaves legacy stock reservations unchanged.

## Verification

Tests cover full/partial/absent inventory, multiple locations, competing orders,
duplicate variant lines, supplier and price validation, tenant isolation,
duplicate clicks, QC and receipt gating, failed reinspection, color rename,
cancellation, protected stock, shipment completion and transaction rollback.
The services are exercised against isolated SQLite fixtures; PostgreSQL locking
and the migration still require verification in the Docker environment.
