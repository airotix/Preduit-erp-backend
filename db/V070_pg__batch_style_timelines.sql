-- Give every style in a production batch its own timeline while retaining
-- previously recorded stage status, dates, workers and history.
BEGIN;
ALTER TABLE production_batch_stages
    ADD COLUMN IF NOT EXISTS production_line_id BIGINT REFERENCES production_order_lines(id);
DROP INDEX IF EXISTS ix_production_batch_stages_production_line_id;
CREATE INDEX ix_production_batch_stages_production_line_id
    ON production_batch_stages (production_line_id);

WITH styles AS (
    SELECT batch_id, production_line_id,
           row_number() OVER (PARTITION BY batch_id ORDER BY production_line_id) AS style_number
    FROM (
        SELECT DISTINCT bl.batch_id, pol.production_line_id
        FROM production_batch_lines bl
        JOIN purchase_order_lines pol ON pol.id = bl.po_line_id
        WHERE pol.production_line_id IS NOT NULL
    ) distinct_styles
)
UPDATE production_batch_stages stage
SET production_line_id = styles.production_line_id
FROM styles
WHERE styles.batch_id = stage.batch_id AND styles.style_number = 1
  AND stage.production_line_id IS NULL;

ALTER TABLE production_batch_stages
    DROP CONSTRAINT IF EXISTS production_batch_stages_batch_id_seq_key;

WITH styles AS (
    SELECT DISTINCT bl.batch_id, pol.production_line_id
    FROM production_batch_lines bl
    JOIN purchase_order_lines pol ON pol.id = bl.po_line_id
    WHERE pol.production_line_id IS NOT NULL
)
INSERT INTO production_batch_stages
    (public_id, tenant_id, batch_id, production_line_id, seq, name, status,
     worker, notes, start_on, end_on, duration_days, actual_start_on, actual_end_on)
SELECT gen_random_uuid(), stage.tenant_id, stage.batch_id, styles.production_line_id,
       stage.seq, stage.name, stage.status, stage.worker, stage.notes,
       stage.start_on, stage.end_on, stage.duration_days,
       stage.actual_start_on, stage.actual_end_on
FROM production_batch_stages stage
JOIN styles ON styles.batch_id = stage.batch_id
WHERE stage.production_line_id IS NOT NULL
  AND styles.production_line_id <> stage.production_line_id
  AND NOT EXISTS (
      SELECT 1 FROM production_batch_stages existing
      WHERE existing.batch_id = stage.batch_id
        AND existing.production_line_id = styles.production_line_id
        AND existing.seq = stage.seq
  );

ALTER TABLE production_batch_stages
    ADD CONSTRAINT uq_batch_style_stage UNIQUE (batch_id, production_line_id, seq);
COMMIT;
