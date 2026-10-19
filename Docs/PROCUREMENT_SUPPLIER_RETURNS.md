# Procurement point 6: supplier return resolution

## Entry points

Procurement → Supplier Returns (`/procurement/returns`) is a paginated register.
Catalog's stock resolution dialog and Finance's supplier ledger link here. Open
returns also appear in the PO change-impact review.

## Physical return

Record one received PO variant and warehouse per return. Partial quantities are
supported; multiple SKU returns may share the supplier's reference. Choose Credit,
Refund, or Replacement and confirm the goods physically left the warehouse.

The server uses saved PO prices, limits the cumulative returned quantity to
received units (including previously replaced units), and rejects missing stock
or reserved quantities. A source batch lot must have enough unallocated stock;
its quantity and the warehouse's batch reservation decrease together. A stock
movement, source PO line, warehouse, quantity, actor, reason, reference and audit
record are retained. Original warehouse receipts and PO commitments are unchanged.

Older stock-only Catalog returns may be linked to a matching PO SKU, warehouse
and exact quantity for credit/refund resolution. This does not deduct stock a
second time. Their missing lot provenance cannot support automatic replacement
receipt. New supplier returns cannot use the old stock-only Catalog endpoint.

## Money resolution

Finance write access is required to confirm the supplier credit-note reference.
The amount comes from the returned quantity and saved PO unit price. The posted
payable must belong to the source PO and supplier. The credit mirrors the original
bill's two journal accounts, debiting AP and crediting its original purchase
account. Original invoices and bills keep their amounts and history. Returns with
unusual, reversed, missing, mismatched or inactive-account journals require Finance
reconciliation instead of an inferred posting. Closed accounting periods block
posting. Cumulative credits cannot exceed the source payable.

Supplier statements, party balances, AP aging, payment defaults and payment
allocation limits include confirmed return credits. A fully settled bill uses the
application's existing Paid status, including settlement by supplier credit.

For Refund, the credit is confirmed first. Finance separately confirms that the
full return value has arrived into Cash or Bank and supplies its receipt reference.
This debits the asset and credits AP. The supplier must have sufficient net advance
balance. Refunds for one supplier are serialized. The supplier statement and cash/
bank journal view include the receipt. Finance can retain a credit against open
payables instead of recording a refund, or change an unresolved replacement to
credit/refund using an explicit reason and reference.

Financial resolution covers the complete quantity on each return record. Split
physical returns into separate records when their settlement terms differ.
Standalone reversal of linked return journals or their original bill journal is
blocked to avoid breaking subledger/GL agreement. Original commercial amounts and
supplier identity cannot be edited after a linked return credit.

## Replacement

A replacement creates a pending, separate inspection in Quality. It does not add
stock or create another payable. Once that exact SKU/quantity inspection passes,
Procurement with Inventory write access confirms receipt of the full replacement
quantity into the original warehouse. Original batch stock is restored to the
same lot when applicable. The movement and resolution history are recorded, and
the return closes. No stock is made available before QC passes. Failed replacements
can be resolved as credit/refund through Finance; inspection history stays intact.
Catalog retirement is blocked while a replacement is outstanding.

## Controls and deployment

Creation and resolution requests use request UUIDs and reject conflicting reuse.
Savepoints keep failed stock/accounting operations atomic. Posted resolution
events retain actor, time, reference and reason; the register displays this history.
Procurement read is required; physical actions require Procurement and Inventory
write. Credit/refund decisions require Finance write, including the Accountant
role which has Procurement read access.

Migration V079_pg__supplier_return_resolution.sql adds the tenant-scoped return
table with RLS and stock/journal links. Applied to local Docker `preduit-db` /
`preduit` and recorded in schema_migrations during implementation.

No tests, builds, type checks, API requests or browser checks were run, following
the user's preference to perform testing themselves. Procurement point 7 is not
part of this change.
