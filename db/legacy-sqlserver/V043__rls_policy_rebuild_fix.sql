/* ============================================================================
 * Preduit ERP — V043__rls_policy_rebuild_fix.sql
 * Fixes V042: STRING_AGG capped at 8000 bytes, so the CREATE SECURITY POLICY
 * step failed and the policy was left DROPPED (RLS temporarily OFF). This
 * re-applies the bypass-aware predicate and rebuilds the policy, casting the
 * aggregate to NVARCHAR(MAX) so the full statement is emitted. Idempotent —
 * safe to run whether or not the policy currently exists. Run after V042.
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
SELECT @preds = STRING_AGG(CONVERT(NVARCHAR(MAX), pred), ',' + CHAR(10))
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

/* Verify: policy exists and is enabled (is_enabled must be 1). */
SELECT name, is_enabled FROM sys.security_policies WHERE name = 'TenantSecurityPolicy';
GO
PRINT 'TenantSecurityPolicy rebuilt with bypass-aware predicate.';
GO
