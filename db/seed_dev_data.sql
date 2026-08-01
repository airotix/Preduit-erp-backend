/* ============================================================================
 * Preduit ERP — seed_dev_data.sql   (DEV ONLY)
 * Creates one demo company and some catalog data so the backend has something
 * real to return. The demo tenant id is FIXED so the backend's dev-login can
 * point at it:  33333333-3333-3333-3333-333333333333
 *
 * Run in SSMS against the Preduit-ERP database, after V001–V003 + create_users.
 * Safe to re-run (guarded with IF NOT EXISTS).
 * ==========================================================================*/
USE [Preduit-ERP];
GO

DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';

/* Insert as the master key so row-security doesn't block us. */
EXECUTE AS USER = 'erp_system';

/* --- company + trial subscription --- */
IF NOT EXISTS (SELECT 1 FROM dbo.tenants WHERE id = @t)
    INSERT INTO dbo.tenants (id, name, slug, base_currency_code)
    VALUES (@t, 'Preduit Demo', 'demo', 'EUR');

IF NOT EXISTS (SELECT 1 FROM dbo.subscriptions WHERE tenant_id = @t)
    INSERT INTO dbo.subscriptions (tenant_id, [plan], status) VALUES (@t, 'trial', 'trialing');

/* --- categories --- */
IF NOT EXISTS (SELECT 1 FROM dbo.categories WHERE tenant_id = @t)
    INSERT INTO dbo.categories (tenant_id, name)
    VALUES (@t, 'Knitwear'), (@t, 'Bottoms'), (@t, 'Shirts');

/* --- colors & sizes --- */
IF NOT EXISTS (SELECT 1 FROM dbo.attribute_values WHERE tenant_id = @t)
    INSERT INTO dbo.attribute_values (tenant_id, attr_type, value, code)
    VALUES (@t, 'Color', 'Navy', 'NVY'), (@t, 'Color', 'Cream', 'CRM'),
           (@t, 'Size', 'M', 'M'),       (@t, 'Size', 'L', 'L');

/* --- products --- */
IF NOT EXISTS (SELECT 1 FROM dbo.products WHERE tenant_id = @t)
    INSERT INTO dbo.products (tenant_id, title, category_id, season, status)
    SELECT @t, 'Merino Crew Knit',
           (SELECT id FROM dbo.categories WHERE tenant_id = @t AND name = 'Knitwear'),
           'Fall ''26', 'Active'
    UNION ALL SELECT @t, 'Tailored Chino',
           (SELECT id FROM dbo.categories WHERE tenant_id = @t AND name = 'Bottoms'),
           'Core', 'Active'
    UNION ALL SELECT @t, 'Oxford Shirt',
           (SELECT id FROM dbo.categories WHERE tenant_id = @t AND name = 'Shirts'),
           'Spring ''26', 'Draft';

/* --- variants (SKU-level, color x size) --- */
IF NOT EXISTS (SELECT 1 FROM dbo.product_variants WHERE tenant_id = @t)
BEGIN
    DECLARE @p1 BIGINT = (SELECT id FROM dbo.products WHERE tenant_id = @t AND title = 'Merino Crew Knit');
    DECLARE @p2 BIGINT = (SELECT id FROM dbo.products WHERE tenant_id = @t AND title = 'Tailored Chino');
    DECLARE @p3 BIGINT = (SELECT id FROM dbo.products WHERE tenant_id = @t AND title = 'Oxford Shirt');
    DECLARE @navy BIGINT = (SELECT id FROM dbo.attribute_values WHERE tenant_id = @t AND code = 'NVY');
    DECLARE @cream BIGINT = (SELECT id FROM dbo.attribute_values WHERE tenant_id = @t AND code = 'CRM');
    DECLARE @m BIGINT = (SELECT id FROM dbo.attribute_values WHERE tenant_id = @t AND code = 'M');
    DECLARE @l BIGINT = (SELECT id FROM dbo.attribute_values WHERE tenant_id = @t AND code = 'L');

    INSERT INTO dbo.product_variants (tenant_id, product_id, sku, color_id, size_id, price, currency_code, status)
    VALUES
      (@t, @p1, 'KNT-NVY-M', @navy, @m, 129.00, 'EUR', 'Active'),
      (@t, @p1, 'KNT-NVY-L', @navy, @l, 129.00, 'EUR', 'Active'),
      (@t, @p1, 'KNT-CRM-M', @cream, @m, 129.00, 'EUR', 'Active'),
      (@t, @p2, 'CHN-NVY-M', @navy, @m,  89.00, 'EUR', 'Active'),
      (@t, @p2, 'CHN-NVY-L', @navy, @l,  89.00, 'EUR', 'Active'),
      (@t, @p3, 'OXF-CRM-M', @cream, @m,  69.00, 'EUR', 'Active');
END

REVERT;
GO

PRINT 'Demo data seeded for tenant 33333333-3333-3333-3333-333333333333.';
GO
