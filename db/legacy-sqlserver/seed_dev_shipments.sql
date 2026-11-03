/* ============================================================================
 * Preduit ERP — seed_dev_shipments.sql  (DEV ONLY)
 * Shipments, carriers, and shipment contents. Run after V019.
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.carriers WHERE tenant_id=@t)
    INSERT INTO dbo.carriers (tenant_id, name, service, avg_transit, on_time_pct, status) VALUES
      (@t, 'DHL Express', 'Air · Express',  '2–3 days',   96, 'Active'),
      (@t, 'FedEx',       'Air · Priority', '3–4 days',   93, 'Active'),
      (@t, 'Maersk LCL',  'Sea · LCL',      '18–24 days', 88, 'Active'),
      (@t, 'Aramex',      'Air · Standard', '4–6 days',   90, 'Active');

IF NOT EXISTS (SELECT 1 FROM dbo.shipments WHERE tenant_id=@t)
BEGIN
    INSERT INTO dbo.shipments (tenant_id, shipment_no, order_ref, carrier, destination, status, eta) VALUES
      (@t, 'SHP-9912', '#SO-12353', 'DHL Express', 'Paris, FR',     'In transit',    '29 Jun'),
      (@t, 'SHP-9911', '#SO-12348', 'FedEx',       'Lisbon, PT',    'Delivered',     '25 Jun'),
      (@t, 'SHP-9910', '#SO-12350', 'Maersk LCL',  'Stockholm, SE', 'Customs',       '04 Jul'),
      (@t, 'SHP-9909', '#SO-12351', 'Aramex',      'Singapore, SG', 'Label created', '01 Jul');

    DECLARE @s BIGINT = (SELECT id FROM dbo.shipments WHERE tenant_id=@t AND shipment_no='SHP-9912');
    INSERT INTO dbo.shipment_lines (tenant_id, shipment_id, sku, description, qty) VALUES
      (@t, @s, 'KNT-NVY-M', 'Merino Crew Knit · Navy · M', 4),
      (@t, @s, 'OXF-CRM-M', 'Oxford Shirt · Cream · M',    3),
      (@t, @s, 'CHN-NVY-M', 'Tailored Chino · Navy · M',   3);
END

REVERT;
GO
PRINT 'Shipments demo data seeded.';
GO
