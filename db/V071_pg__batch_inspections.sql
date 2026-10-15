-- Link batch QC submissions to real Quality inspection records and quantities.
BEGIN;
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS batch_line_id BIGINT REFERENCES production_batch_lines(id);
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS approved_qty INTEGER;
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS held_qty INTEGER;
ALTER TABLE inspections ADD COLUMN IF NOT EXISTS rejected_qty INTEGER;
CREATE INDEX IF NOT EXISTS ix_inspections_batch_line_id ON inspections(batch_line_id);

-- Earlier batch QC commands kept their dispositions and an inspection UUID in
-- the batch event ledger. Materialize those records without changing totals.
WITH legacy AS (
    SELECT event.*,
           CASE WHEN (event.result::jsonb ->> 'inspectionId') ~
                     '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                THEN (event.result::jsonb ->> 'inspectionId')::uuid
                ELSE md5('batch-qc:' || event.id::text)::uuid END AS inspection_id
    FROM production_batch_events event
    WHERE event.kind = 'QC' AND (event.payload::jsonb ->> 'scope') ~ '^[0-9]+$'
)
INSERT INTO inspections
    (public_id, tenant_id, inspection_no, order_ref, item, stage, aql,
     defect_count, result, inspector, sku, product, batch_lot, inspection_type,
     prod_qty, sample_size, inspection_date, finalized_at, disposition_notes, batch_line_id,
     approved_qty, held_qty, rejected_qty, is_deleted)
SELECT legacy.inspection_id, legacy.tenant_id, 'QC-B' || legacy.id::text,
       work.order_no,
       concat_ws(' / ', source.name, source.color, source.size),
       CASE WHEN legacy.payload::jsonb ->> 'kind' = 'Inline' THEN 'In-line' ELSE 'Final' END,
       '2.5', 0,
       CASE WHEN coalesce((legacy.payload::jsonb ->> 'held')::int, 0) = 0
                 AND coalesce((legacy.payload::jsonb ->> 'rejected')::int, 0) = 0
            THEN 'Pass' ELSE 'Fail' END,
       legacy.actor, source.sku, source.name, left(batch.name, 60),
       legacy.payload::jsonb ->> 'kind',
       (legacy.payload::jsonb ->> 'assessed')::int,
       (legacy.payload::jsonb ->> 'sampleSize')::int,
       legacy.created_at::date, legacy.created_at, legacy.payload::jsonb ->> 'reason', line.id,
       CASE WHEN legacy.payload::jsonb ->> 'kind' = 'Inline' THEN 0 ELSE (legacy.payload::jsonb ->> 'approved')::int END,
       CASE WHEN legacy.payload::jsonb ->> 'kind' = 'Inline' THEN 0 ELSE (legacy.payload::jsonb ->> 'held')::int END,
       CASE WHEN legacy.payload::jsonb ->> 'kind' = 'Inline' THEN 0 ELSE (legacy.payload::jsonb ->> 'rejected')::int END,
       FALSE
FROM legacy
JOIN production_batch_lines line ON line.id = (legacy.payload::jsonb ->> 'scope')::bigint
JOIN production_batches batch ON batch.id = line.batch_id
JOIN production_orders work ON work.id = batch.order_id
JOIN purchase_order_lines source ON source.id = line.po_line_id
ON CONFLICT (public_id) DO NOTHING;

UPDATE production_batch_events event
SET result = jsonb_set(event.result::jsonb, '{inspectionId}',
    to_jsonb(md5('batch-qc:' || event.id::text)::uuid))::text
WHERE event.kind = 'QC'
  AND (event.payload::jsonb ->> 'scope') ~ '^[0-9]+$'
  AND NOT (event.result::jsonb ? 'inspectionId');
COMMIT;
