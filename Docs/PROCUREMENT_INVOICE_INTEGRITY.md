# Procurement point 2: PO, invoice and payable consistency

Generated invoice matrices group by article, colour AND saved unit price. Sizes
with different prices are separate rows; a zero-price row is not replaced by
another size's positive price. Monetary calculations use Decimal with explicit
currency rounding. Any difference between rounded display rows and the rounded
saved PO total is shown as a rounding adjustment. A PO whose header disagrees
with its saved lines cannot generate an invoice until reconciled.

The backend validates invoice quantities and unit prices against the original PO
on creation and update, then calculates all derived values itself. Changing the
breakdown while retaining the same grand total is rejected. PO identity, supplier,
currency and freight cannot be independently changed. Descriptions, article
presentation details, contact information, and banking details remain editable.
Catalog prices are not consulted when generating an existing PO's invoice.

The editor makes financial fields read only. On opening a document, consistent
PO/payable values produce the canonical financial breakdown for display; saving
persists it. Historical mismatched payables show a reconciliation warning and
cannot be saved. The system does not rewrite posted Finance entries. After the
payable has been reconciled to the PO, the document can be corrected with an
audit entry. Currency disagreements also require reconciliation. Existing
standalone legacy documents retain their protected financial values.

No database migration is required. Tests and runtime checks were not run, in
accordance with the user's instruction to test themselves. Other Procurement
points, including supplier price setup and cancellation-impact handling, remain
separate work.
