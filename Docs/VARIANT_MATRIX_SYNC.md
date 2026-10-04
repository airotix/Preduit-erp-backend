# Catalogue and Inventory variant matrices

Both matrix PUT endpoints now call the shared `inventory.matrix.save_matrix`
service. Both detail views read active product variants and summed stock levels.
Both screens always display the standard XS–7XL size columns, including for an
empty article. Existing non-standard sizes are appended to preserve their stock.

The editors display fixed size headers and editable quantities; colour rows can
be added, renamed or removed. Size add/rename/delete controls are hidden.
`originalColor` and `originalSize` in the matrix payload identify renamed cells,
preserving the variant ID, SKU, purchasing/channel prices and stock references.
Omitted empty variants are marked `Archived` rather than physically deleted, so
historical document references survive. Readding a combination reuses its SKU.
Removing a variant with on-hand stock or reservations returns 409. Quantity
updates preserve reservations and set the total across locations, rather than
duplicating the total into one warehouse. Receiving stock reactivates an archived
variant so it is visible in both modules.

The shared save is transactional through the existing tenant DB dependency and
serializes matrix edits by locking the article. Catalogue quantities now come
from inventory stock levels; the legacy variant quantity field is no longer the
source for matrix display. Newly created variants inherit all channel prices.

No database schema migration is required for this change. Restart the backend
and reload/rebuild the frontend. Existing matrices synchronize when read; edits
to either screen update the common records. Historical catalogue-only quantity
values do not overwrite inventory balances.

When positive stock is saved for an article without an assigned location, both
editors open a modal. The user can select an existing location or create one with
name, code, Warehouse/Retail type and region (capacity is optional). The modal
then retries the pending matrix with `location` while retaining entered quantities.
Location creation uses the normal inventory permissions. Cancelling leaves edits
in place. Existing article locations are reused on subsequent saves.

Verification: `python -m unittest discover -s tests -v` from backend and
`npm run typecheck` from frontend. Regression tests use an isolated SQLite DB;
PostgreSQL locking/RLS require an integration environment.
