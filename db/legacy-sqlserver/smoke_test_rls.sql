/* ============================================================================
 * Preduit ERP — smoke_test_rls.sql   (OPTIONAL — run LAST, after V003)
 *
 * Proves the tenant isolation works: it creates two pretend companies, then
 * shows that the everyday app key can only ever see ONE of them at a time.
 * Safe to run on a dev database. Cleanup at the bottom removes the test data.
 * ==========================================================================*/
USE preduit;
GO

/* --- 1. Create two test companies, using the master key (erp_system) ------ */
EXECUTE AS USER = 'erp_system';

IF NOT EXISTS (SELECT 1 FROM dbo.tenants WHERE slug = 'acme')
    INSERT INTO dbo.tenants (id, name, slug, base_currency_code)
    VALUES ('11111111-1111-1111-1111-111111111111', 'Acme',   'acme',   'USD');

IF NOT EXISTS (SELECT 1 FROM dbo.tenants WHERE slug = 'globex')
    INSERT INTO dbo.tenants (id, name, slug, base_currency_code)
    VALUES ('22222222-2222-2222-2222-222222222222', 'Globex', 'globex', 'EUR');

REVERT;
GO

/* --- 2. Act as the everyday app key, scoped to Acme ----------------------- */
EXECUTE AS USER = 'erp_app';
EXEC sp_set_session_context @key = N'tenant_id',
     @value = '11111111-1111-1111-1111-111111111111';

SELECT '--- Scoped to Acme (expect ONLY Acme) ---' AS test;
SELECT name, slug FROM dbo.tenants;

REVERT;
GO

/* --- 3. Same key, switched to Globex -------------------------------------- */
EXECUTE AS USER = 'erp_app';
EXEC sp_set_session_context @key = N'tenant_id',
     @value = '22222222-2222-2222-2222-222222222222';

SELECT '--- Scoped to Globex (expect ONLY Globex) ---' AS test;
SELECT name, slug FROM dbo.tenants;

REVERT;
GO

/* If each SELECT returns exactly one company, tenant isolation is working. */

/* --- 4. Cleanup: remove the test companies -------------------------------- */
EXECUTE AS USER = 'erp_system';
DELETE FROM dbo.tenants WHERE slug IN ('acme', 'globex');
REVERT;
GO
