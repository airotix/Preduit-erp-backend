# Article suppliers and supplier shipment groups

The New PO form selects the supplier separately on each article row. Options and
colour/size prices come from that article's saved Catalog supplier agreements.
Prices are resolved again on the server; submitted prices are not authoritative.

One form submission creates one legal purchase order per supplier. All articles
assigned to the same supplier in that submission share their PO, invoice and
production order. Each style retains its own production TNA and final inspection.
The confirmation identifies each created PO and supplier. Unrelated existing POs
are not merged. The expected date entered in the form applies to each resulting PO.

Creation is atomic across all supplier groups. A request UUID and recorded result
make a retry return the same POs, without duplicate production work or payables.

For these new POs, once all style TNAs are complete and their latest final QC runs
have passed, one Pending inbound shipment is created for the supplier PO. Passing
only one article does not create a shipment containing uncleared articles. Different
supplier POs reach this gate independently. Pending shipments do not change stock.
Shipment contents retain their exact PO line links, including colour and size.

Shipments manages carrier, destination and dispatch. Dispatch rechecks approval,
TNA and QC. Carrier/destination cannot change after dispatch. A cancelled or
withdrawn PO cannot dispatch.
Preparation can be cancelled manually in Shipments before dispatch, including
after PO withdrawal. Dispatched production work cannot be restarted through TNA.
Inventory's warehouse receiving records actual
received quantities; only the final receipt marks the supplier shipment Delivered.
Partial receipts keep the balance open. Sales fulfilment after warehouse receipt
continues to use warehouse reservations and customer shipments.

Existing POs keep their original receipt flow. Batch production keeps its existing
quantity-based inbound shipment controls and can still have partial shipments;
each batch inbound shipment is linked to its one supplier PO. The new ordinary
supplier shipment is not created for batch-managed production.

Business rationale: supplier-specific POs keep commercial liability, invoices,
approval and supplier performance attributable to one counterparty. Consolidating
articles from one supplier is appropriate when they travel together to the same
destination. Different carriers, destinations or partial batch deliveries represent
separate physical shipments and use the existing batch shipment controls.

Reference: [ERPNext Purchase Order](https://docs.frappe.io/erpnext/purchase-order)
describes a PO as a supplier contract with linked receipts and supplier invoices.

Schema: `db/V081_pg__supplier_po_shipments.sql`. Existing rows are not backfilled
with shipment requirements. No automated tests or builds were run for this change,
as requested; the user will perform workflow testing.
