# Sales points 1 and 5

## Catalog-controlled prices

Orders require saved active Catalog variants. The server selects the Wholesale,
Retail, or Online price (Marketplace uses Online), ignores submitted unit prices,
and snapshots the resolved SKU, colour, size, variant ID and price on each line.
The current sales flow uses EUR; other Catalog currencies are rejected.

The order form displays read-only prices. Invoice generation uses saved order
prices rather than current Catalog prices. Wholesale rows with different prices
are kept separate even when they have the same colour. Source-linked invoice
documents protect quantities, prices, currency, customer identity and totals on
creation and save; their other presentation/contact fields remain editable.
Opening an older linked document displays the original order's financial values.
Catalog price changes affect future orders only. Existing order/receivable prices
are not rewritten. Existing billing timing and payment behavior remain in place.

## Ordinary partial shipments and packing

New orders default to Ship complete. Allow partial shipments can be selected on
the form or changed on an ordinary order before packing/dispatch. Available stock
remains reserved for its source order. A ready order creates a Pending shipment
and becomes Ready to pack, not Packed.

Shipments → shipment detail shows Packing confirmation. Warehouse staff enter the
actual packed quantities. Ship-complete orders must contain all outstanding units;
partial orders may contain a positive subset of reserved units. Confirmation fixes
the contents and records the user, note and time. Dispatch requires confirmed
packing and consumes only these line quantities from the order-owned holds.
Repeated dispatch does not consume stock again. Delivery records customer receipt.

After a partial dispatch, the main order stays Partially shipped and retains its
outstanding quantities and stock reservations. A subsequent available portion
creates the next Pending shipment. All dispatches are linked to the same order;
only full quantity coverage makes it Shipped. Return authorization is limited to
the actual dispatched quantities, rather than all quantities originally ordered.

Existing batch workflows remain responsible for batch packing/dispatch. When a
partially shipped ordinary order later enters batch production, its earlier
ordinary dispatch quantities are included in cumulative fulfillment. Unpacked
ordinary drafts are cancelled at batch planning; physically packed drafts must
be dispatched first. Legacy ambiguous or complete whole-order shipments require
reconciliation before batch conversion.
Ordinary dispatched portions remain eligible for ordinary RMAs after batch
conversion; batch-dispatched portions retain their separate batch return flow.

V077 adds shipment-line identity, order policy and packing metadata. Historical
dispatches are preserved; previously auto-Packed ordinary pending orders become
Ready to pack and need explicit packing confirmation. Tests and runtime checks
were not run, following the user's existing instruction to test themselves.
