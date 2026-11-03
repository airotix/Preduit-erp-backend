/* ============================================================================
 * Preduit ERP — seed_dev_inventory.sql  (DEV ONLY)
 * Locations, stock, a couple transfers and a reorder alert for the demo tenant.
 * Location names match the frontend form enums so create forms resolve them.
 * Run after V008. Safe to re-run.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* Locations */
IF NOT EXISTS (SELECT 1 FROM dbo.locations WHERE tenant_id=@t)
    INSERT INTO dbo.locations (tenant_id, name, code, [type], region, capacity) VALUES
      (@t, 'Lahore DC',      'WH-LHE-01', 'Warehouse', 'Pakistan', 2000),
      (@t, 'Karachi DC',     'WH-KHI-02', 'Warehouse', 'Pakistan', 1500),
      (@t, 'Dubai DC',       'WH-DXB-01', 'Warehouse', 'UAE',      1200),
      (@t, 'Flagship Store', 'RT-LHE-01', 'Retail',    'Pakistan', 400);

/* Stock at Lahore DC for the seeded variants */
IF NOT EXISTS (SELECT 1 FROM dbo.stock_levels WHERE tenant_id=@t)
BEGIN
    DECLARE @lhe BIGINT = (SELECT id FROM dbo.locations WHERE tenant_id=@t AND name='Lahore DC');
    INSERT INTO dbo.stock_levels (tenant_id, variant_id, location_id, on_hand, reserved)
    SELECT @t, v.id, @lhe,
           CASE v.sku WHEN 'KNT-NVY-M' THEN 120 WHEN 'KNT-NVY-L' THEN 64
                      WHEN 'KNT-CRM-M' THEN 18  WHEN 'CHN-NVY-M' THEN 42
                      WHEN 'CHN-NVY-L' THEN 0   WHEN 'OXF-CRM-M' THEN 30 ELSE 0 END,
           CASE v.sku WHEN 'KNT-NVY-M' THEN 20  WHEN 'KNT-NVY-L' THEN 10 ELSE 0 END
    FROM dbo.product_variants v WHERE v.tenant_id=@t;
END

/* Transfers */
IF NOT EXISTS (SELECT 1 FROM dbo.stock_transfers WHERE tenant_id=@t)
BEGIN
    DECLARE @lhe2 BIGINT = (SELECT id FROM dbo.locations WHERE tenant_id=@t AND name='Lahore DC');
    DECLARE @dxb BIGINT  = (SELECT id FROM dbo.locations WHERE tenant_id=@t AND name='Dubai DC');
    DECLARE @khi BIGINT  = (SELECT id FROM dbo.locations WHERE tenant_id=@t AND name='Karachi DC');
    INSERT INTO dbo.stock_transfers (tenant_id, transfer_no, from_location_id, to_location_id, units, status, eta) VALUES
      (@t, 'TRF-2041', @lhe2, @dxb, 1200, 'In transit', '02 Jul'),
      (@t, 'TRF-2040', @khi,  @lhe2, 640, 'Received',   '24 Jun');
END

/* Reorder alert */
IF NOT EXISTS (SELECT 1 FROM dbo.reorder_alerts WHERE tenant_id=@t)
    INSERT INTO dbo.reorder_alerts (tenant_id, sku, available, reorder_point, suggested, supplier, severity)
    VALUES (@t, 'CHN-NVY-L', 0, 40, 200, 'Anhui Knit Mills', 'Critical');

REVERT;
GO
PRINT 'Inventory demo data seeded.';
GO
