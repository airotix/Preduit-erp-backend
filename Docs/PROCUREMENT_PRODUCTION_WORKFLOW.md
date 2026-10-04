# Procurement-driven production

Every new procurement PO creates exactly one production work order in the same
database transaction. Sales order creation no longer creates production work.
PO approval/status changes do not create another work order. Standalone POST
`/api/v1/production/porders` returns 409; existing work remains accessible.

The production entry starts with no stages. Operators start TNA per style using
the existing Production interface. All variant PO lines link to the corresponding
production style item; quantities are summed per style. Source PO details retain
SKU, size, colour and purchasing prices. PO and production detail pages link to
each other using stable public IDs.

## Database rollout

The current backend uses PostgreSQL. Before running the updated backend against
an existing database, apply `db/V063_pg__procurement_production.sql` as the database
owner/migration role. The script is transactional and safe to reapply. Fresh
databases use the updated `V001_pg__consolidated_schema.sql`.

The container entrypoint skips consolidated schema setup for existing databases;
it does not automatically apply this upgrade. Example with an already configured
PostgreSQL connection: `psql -v ON_ERROR_STOP=1 -f db/V063_pg__procurement_production.sql`.

No historical POs are backfilled and no existing production work or TNA progress
is deleted. Legacy sales links remain available for shipment/order-history lookup.
New procurement work has no inferred sales allocation. Stock-shortage routing,
sales allocation, QC inventory receipt, and shipment-policy changes are separate
workflow changes and are not introduced here.

## Verification

Run `python -m unittest discover -s tests -v` from `backend`, and
`npm run typecheck` from `frontend`. Backend regression tests exercise PO-derived
work, style grouping/variant links, atomic rollback, idempotency, tenant isolation,
sales-only creation, blocked standalone creation, and starting TNA explicitly.
