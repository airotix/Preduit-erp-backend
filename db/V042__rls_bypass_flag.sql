/* ============================================================================
 * Preduit ERP — V042__rls_bypass_flag.sql
 * Local-dev / provisioning fix.
 *
 * The tenant predicate only exempts the `erp_system` DB principal. On LocalDB
 * with Windows auth the backend connects as dbo (NOT erp_system), so the
 * "system" connection was still filtered by RLS — breaking cross-tenant
 * provisioning reads (find-user-by-email at login/verify/reset, company list).
 *
 * This adds a second, controlled bypass: a connection may opt out of RLS by
 * setting SESSION_CONTEXT('rls_bypass') = 1. ONLY the backend's system session
 * sets it (see core/database.system_session); the RLS-scoped app session never
 * does, and its pool checkout clears it defensively. Every tenant-scoped read on
 * the system session still filters by tenant_id explicitly or uses unique keys,
 * so this cannot leak data across tenants.
 *
 * The security policy is dropped and rebuilt dynamically over every dbo table
 * that has a tenant_id column (plus dbo.tenants keyed on id), so it also
 * re-covers tables added since V002 in one shot. Run after V041.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'TenantSecurityPolicy')
    DROP SECURITY POLICY dbo.TenantSecurityPolicy;
GO

CREATE OR ALTER FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS is_visible
    WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)
       OR DATABASE_PRINCIPAL_ID('erp_system') = DATABASE_PRINCIPAL_ID()
       OR CAST(SESSION_CONTEXT(N'rls_bypass') AS BIT) = 1;
GO

DECLARE @preds NVARCHAR(MAX);
SELECT @preds = STRING_AGG(pred, ',' + CHAR(10))
FROM (
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants' AS pred
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(id) ON dbo.tenants AFTER INSERT'
    UNION ALL
    SELECT N'    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name)
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
    UNION ALL
    SELECT N'    ADD BLOCK PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.' + QUOTENAME(t.name) + N' AFTER INSERT'
    FROM sys.tables t
    JOIN sys.columns c ON c.object_id = t.object_id AND c.name = 'tenant_id'
    WHERE t.schema_id = SCHEMA_ID('dbo')
) x;

DECLARE @sql NVARCHAR(MAX) =
    N'CREATE SECURITY POLICY dbo.TenantSecurityPolicy' + CHAR(10) + @preds + CHAR(10) + N'    WITH (STATE = ON);';
EXEC sys.sp_executesql @sql;
GO
PRINT 'RLS bypass flag added; TenantSecurityPolicy rebuilt.';
GO
