/* ============================================================================
 * Preduit ERP — seed_dev_production.sql  (DEV ONLY)
 * Production orders (across stages) + bill-of-materials lines. Run after V017.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.production_orders WHERE tenant_id=@t)
    INSERT INTO dbo.production_orders (tenant_id, order_no, style, factory, qty, stage, progress) VALUES
      (@t, 'MO-3308', 'Oxford Shirt',      'Lahore Unit 1', 5000, 'Finishing', 88),
      (@t, 'MO-3309', 'Tailored Chino',    'Faisalabad',    3000, 'Cutting',   28),
      (@t, 'MO-3310', 'Merino Crew Knit',  'Lahore Unit 2', 2400, 'Sewing',    62),
      (@t, 'MO-3312', 'Linen Camp Shirt',  'Lahore Unit 1', 1800, 'Cutting',   10),
      (@t, 'MO-3305', 'Cashmere Scarf',    'Sialkot',       1000, 'Completed', 100);

IF NOT EXISTS (SELECT 1 FROM dbo.bill_of_materials WHERE tenant_id=@t)
    INSERT INTO dbo.bill_of_materials (tenant_id, component, style, material, qty_per_unit, cost) VALUES
      (@t, 'Merino yarn 2/28',     'Merino Crew Knit', 'Wool',   '320 g', 14.20),
      (@t, 'Cotton twill 280gsm',  'Tailored Chino',   'Cotton', '1.4 m', 6.80),
      (@t, 'Corozo button 18L',    'Oxford Shirt',     'Trim',   '9 pcs', 0.54),
      (@t, 'Oxford cotton 140gsm', 'Oxford Shirt',     'Cotton', '2.1 m', 5.90);

REVERT;
GO
PRINT 'Production demo data seeded.';
GO
