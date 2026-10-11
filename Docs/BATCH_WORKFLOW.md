# Production batches, QC and partial shipments

Open a production order and use its **Batch production & shipments** workspace. Sales orders and POs show the same underlying batches and shipment records. Expand a batch to work on its stages, quantities and history. Production and Quality lists also link directly to batch workspaces.

## Daily workflow

1. **Plan a batch.** Name it, set its factory and due date, choose quantities per article/color/size, and enter its ordered production stages. Several batches can belong to one production order. Plan only the remaining order quantity; recorded scrap permits replacement production. Cancel an incorrect plan before recording completed pieces; the cancelled plan remains in history and restores planning capacity.
2. **Record production.** Start and complete each stage in sequence, then record completed pieces for each variant. Partial completion is allowed. Completing a stage alone does not create stock or approve pieces.
3. **Record QC.** Enter the lot quantity assessed, the sample inspected, and approved, held/rework and rejected/scrap quantities. The three disposition quantities must total the assessed lot; sample size is separate and cannot exceed it. Inline QC records an observation. Final QC approves previously unassessed completed pieces. Reinspection redistributes held pieces after rework; it does not count them as newly produced.
4. **Receive approved stock.** Choose a warehouse and quantity. Receipt is limited to approved pieces not yet received and creates an inventory receipt. Held, rejected and unassessed pieces cannot be received or shipped. Received batch stock stays earmarked for its order. Use the batch transfer action to move unallocated pieces between warehouses while retaining batch identity.
5. **Create a shipment.** Choose how many pieces of each variant to send. The form suggests allocations from available batch stock; expand sources to adjust them. One shipment can combine batches, and a batch can supply several shipments. From the sales order workspace, existing reserved warehouse inventory can be included alongside new batch stock. Every allocation retains its batch and warehouse, or its existing inventory origin where the original receipt has no batch identity.
6. **Confirm dispatch.** Creating a pending shipment reserves its allocation. Confirm dispatch only when pieces leave the warehouse; this consumes stock and updates the order's dispatched balance. A pending shipment can be cancelled to release its allocation. Record transit/customs/delivery progress afterward. The order becomes fully shipped only when its entire quantity has actually been dispatched; delivered quantity is tracked separately.

Production staff enter production quantities, QC staff enter dispositions, warehouse staff enter receipts/transfers, and shipping staff enter allocations and dispatch. The summary totals are calculated from these records, not editable counters. Commands record the user, time and reason and use request IDs to prevent a retried submission from changing stock twice.

## Reading quantities

- **Produced** is gross completed production, including replacement pieces. Production progress excludes recorded scrap and is capped at the parent order quantity.
- **Awaiting QC** is completed quantity without a final disposition. **Hold** needs reinspection. **Approved** is cumulative approved quantity, including pieces later received or dispatched.
- **Received** is cumulative approved warehouse receipts. **Available** is currently held warehouse stock minus pending shipment allocations. Receipt does not mean dispatch.
- **Allocated** belongs to pending shipments. **Dispatched** has left the warehouse. **Delivered** is the subset recorded as delivered. **Remaining** is order quantity minus net dispatched quantity.

## Boundaries and reconciliation

Existing orders with an earlier whole-order shipment require reconciliation before batch planning. The system prevents mixing that earlier automatic shipment with new batch allocations.

A full physical return of a batch-only shipment can be recorded with inventory permission. Returned pieces go to QC hold, reduce net dispatched quantity, and require reinspection and a fresh approved receipt before reshipping. Partial returns and returns of shipments containing older inventory sources continue to require reconciliation through the existing returns process; they are not silently restored as approved batch stock.

Production, QC, inventory and shipment write permissions control their respective actions. Read access to a batch workspace is available through the relevant module permissions. All batch tables have tenant policies, and allocation commands lock their parent order and stock to prevent concurrent over-allocation.

## Deployment

Deploy the backend and frontend together and apply `db/V068_pg__batch_execution.sql` using the normal migration runner and migration database role. It adds the batch ledgers and permits several managed shipments per sales order while preserving the earlier single-shipment rule for legacy shipments. It does not rewrite existing orders, shipments or inventory provenance. The schema has already been applied to the local Docker database; production deployment is separate.

Validation: backend business/API regression tests, real PostgreSQL workflows in a disposable schema, concurrent allocation, installed PostgreSQL tenant isolation, frontend type checks, frontend tests and an optimized production build.
