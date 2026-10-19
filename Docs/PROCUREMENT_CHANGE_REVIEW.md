# Procurement point 4: PO change impact review

## User flow

- Open a PO: **PO changes and linked commitments** supports cancellation, inactivation and expected-delivery changes.
- Rejecting a pending PO in the Approval Queue uses the same review dialog.
- The dialog lists production TNAs, pending inspections, shipments, receipts, commercial invoices, supplier bills, payments and the source sales order, with a reference, current status, manual follow-up instruction and module link.
- A reason and explicit acknowledgement are mandatory. The server rechecks the current impact and rejects stale reviews.
- Physical receipts, completed batch output, progressed shipments and allocated/settled payments block withdrawal. Their accounting and stock histories cannot be undone by changing a PO status.
- Linked records are retained. Withdrawal no longer silently cancels production, inspections or shipment allocations. It does not delete/reverse Finance entries or change the source sales order.
- After withdrawal, the PO detail retains a live dependency list. The confirmation dialog also remains open after saving so the user can open each linked module.
- In Production, a withdrawn source PO exposes **Stop production TNA**. A production user supplies a reason and explicitly closes its unfinished stages and batches. Completed stages and all linked inspections/stock/shipping/Finance records remain. Completed batch pieces must first be reconciled.
- Production stage starts/completions and batch output recording require an Approved source PO, including already-started stages after withdrawal.
- Invoice auto-generation and manual invoice creation cannot recreate documents against withdrawn POs.
- Commercial quantities, supplier and price commitments remain immutable in this workflow. Use withdrawal plus a replacement PO for a commercial revision. Expected-delivery changes do not rewrite invoice delivery dates or reschedule production stages automatically.

## API

- `GET /procurement/pos/{id}/impact?status=Cancelled|Rejected|Inactive` previews withdrawal.
- `GET /procurement/pos/{id}/impact?expected=...` previews an expected-delivery change.
- `GET /procurement/pos/{id}/impact` returns current linked commitments.
- `POST /procurement/pos/{id}/status` requires `reason`, `reviewToken`, and `acknowledged: true` for withdrawal.
- `PUT /procurement/pos/{id}/expected` requires the same review fields and `expected`.
- `POST /production/porders/{id}/stop` requires a reason and Production write access, after source PO withdrawal.
- Legacy Catalog PO-inactivation calls are rejected with a direction to the Procurement impact review.

The PO audit records the actor, before/after values, reason, review fingerprint and acknowledged follow-up list. Existing audit tables are used; no database migration is needed.

No tests, builds, type checks, browser checks or runtime validation were run, following the user's preference to test manually.
