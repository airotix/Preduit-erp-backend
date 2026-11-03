/* PostgreSQL port of ../seed_dev_quality.sql (DEV ONLY). Three inspections and defect types. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM inspections WHERE tenant_id=t) THEN
    INSERT INTO inspections (tenant_id, inspection_no, order_ref, stage, aql, defect_count, result, inspector) VALUES
      (t,'QC-7740','MO-3308','Final', '2.5', 3,'Pass','S. Marino'),
      (t,'QC-7739','MO-3310','Inline','2.5',11,'Fail','A. Khan'),
      (t,'QC-7738','MO-3309','Final', '4.0', 6,'Pass','S. Marino');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM defect_types WHERE tenant_id=t) THEN
    INSERT INTO defect_types (tenant_id, name, category, severity, frequency) VALUES
      (t,'Broken stitch','Stitching','Major',32),
      (t,'Skipped stitch','Stitching','Minor',21),
      (t,'Color shading','Fabric','Major',14),
      (t,'Loose button','Trim','Minor',9);
  END IF;
END $$;
