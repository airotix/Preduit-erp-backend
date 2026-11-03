/* ============================================================================
 * Preduit ERP — seed_dev_quality.sql  (DEV ONLY)
 * Inspections + defect types. Run after V018.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.inspections WHERE tenant_id=@t)
    INSERT INTO dbo.inspections (tenant_id, inspection_no, order_ref, stage, aql, defect_count, result, inspector) VALUES
      (@t, 'QC-7740', 'MO-3308', 'Final',  '2.5', 3,  'Pass', 'S. Marino'),
      (@t, 'QC-7739', 'MO-3310', 'Inline', '2.5', 11, 'Fail', 'A. Khan'),
      (@t, 'QC-7738', 'MO-3309', 'Final',  '4.0', 6,  'Pass', 'S. Marino');

IF NOT EXISTS (SELECT 1 FROM dbo.defect_types WHERE tenant_id=@t)
    INSERT INTO dbo.defect_types (tenant_id, name, category, severity, frequency) VALUES
      (@t, 'Broken stitch',  'Stitching', 'Major', 32),
      (@t, 'Skipped stitch', 'Stitching', 'Minor', 21),
      (@t, 'Color shading',  'Fabric',    'Major', 14),
      (@t, 'Loose button',   'Trim',      'Minor', 9);

REVERT;
GO
PRINT 'Quality demo data seeded.';
GO
