# Inventory points 4 and 5

## Replenishment

Inventory → Reorder Alerts now calculates proposals from saved rules for an exact
variant and warehouse. Available = on hand − reserved; projected stock also
includes remaining units on standalone, active stock POs assigned to that
warehouse. Sales-linked procurement is earmarked for its source order and is
excluded. Unassigned incoming POs block another proposal for their variants until
the incoming warehouse is assigned.

Rules specify minimum, target, supplier, lead time in days, minimum order and
pack multiple. When projected stock is at or below minimum, the suggested PO
quantity restores the target, rounded for the order minimum and pack multiple.
Creating a proposal makes a real PO through the normal procurement, production
and payable flow. It requires inventory and procurement write access, a released
article and a positive supplier price. Existing PO generation uses EUR; other
currencies are rejected rather than mispriced. Receiving still records the actual
warehouse and actual partial quantities after final QC.

Old static reorder-alert rows are retained as legacy data but do not drive the
new screen or dashboard. Configure rules to enable live replenishment.

## Customer returns

Sales → Returns authorizes quantities from a dispatched ordinary sales order.
Concurrent authorizations cannot exceed the unreturned shipped quantities.
Inventory → Customer Returns records partial physical receipt into quarantine.
Quality → Return QC assigns received quantities to approved restock, repair or
scrap. Repaired goods require a further inspection. Inventory then puts approved
goods away, increasing stock exactly once and recording the movement. Sales can
cancel an unreceived RMA or close unreceived authorization after all received
units have been processed. Quarantine and scrap never increase sellable stock.

Batch shipment returns retain the existing Returned → batch QC flow. They are
not mixed into ordinary stock returns. Historical metadata-only RMAs are shown
read only and cannot post physical movements. Requested refunds are metadata;
this workflow does not create accounting credit notes or payments.

## Stocktakes

Inventory → Stocktakes supports a warehouse count or selected-variant cycle
count with an assigned, active inventory user. Save counts and submit for review;
neither action changes stock. A variance over five units OR ten percent requires
a recount. Significant variances need a reviewer who neither created nor counted
the session. An administrator may explicitly override the independent-review
requirement with a recorded reason, but still must obtain a recount.

Approval validates the current balance and physical-movement watermark against
the snapshot. A changed balance must be restarted and recounted, even if a later
movement restored the same numeric balance. Counts cannot remove reserved or
batch-traced stock. Posting creates adjustment ledger entries and updates the
variant stock cache; added units can fulfill waiting orders. Closed sessions
cannot be edited. Action history retains counts, snapshots, actor and reason.
The former immediate count-correction endpoint is blocked; the Catalog count
dialog submits a single-variant stocktake for review instead.

Migration: `V076_pg__warehouse_planning_returns_stocktakes.sql`, applied to the
local Docker database. Tests and runtime checks were not run at the user's request.
