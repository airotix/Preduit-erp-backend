/* ============================================================================
 * Preduit ERP — V002__row_level_security.sql
 * Tenant isolation enforced in the database (plan §2).
 *
 * The app sets the current tenant on every connection checkout:
 *     EXEC sp_set_session_context @key=N'tenant_id', @value=<guid>, @read_only=1;
 * The predicate below then filters/blocks every row that doesn't match.
 *
 * A privileged "system" principal (used only for provisioning/admin jobs) is
 * exempted so it can create tenants and seed defaults across the boundary.
 * Set its name via the SQLCMD variable :setvar SYSTEM_PRINCIPAL, default 'erp_system'.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* Predicate: row is visible when its tenant matches SESSION_CONTEXT('tenant_id'),
 * OR the connection runs as the system principal (bypass for provisioning). */
CREATE FUNCTION dbo.fn_tenant_predicate(@tenant_id UNIQUEIDENTIFIER)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS is_visible
    WHERE @tenant_id = CAST(SESSION_CONTEXT(N'tenant_id') AS UNIQUEIDENTIFIER)
       OR DATABASE_PRINCIPAL_ID('erp_system') = DATABASE_PRINCIPAL_ID();
GO

/* One policy covering every tenant-scoped table.
 * FILTER hides other tenants' rows on read; BLOCK stops writing rows for another
 * tenant. The tenants table is matched on its own id column. */
CREATE SECURITY POLICY dbo.TenantSecurityPolicy
    ADD FILTER PREDICATE dbo.fn_tenant_predicate(id)        ON dbo.tenants,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(id)        ON dbo.tenants AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.subscriptions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.subscriptions AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.users,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.users AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.roles,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.roles AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.role_permissions,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.role_permissions AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.user_roles,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.user_roles AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.exchange_rates,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.exchange_rates AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.audit_log,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.audit_log AFTER INSERT,

    ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.system_settings,
    ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.system_settings AFTER INSERT
    WITH (STATE = ON);
GO

/* NOTE: as new tenant-scoped tables are added in later migrations, extend this
 * policy with:
 *   ALTER SECURITY POLICY dbo.TenantSecurityPolicy
 *     ADD FILTER PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.<table>,
 *     ADD BLOCK  PREDICATE dbo.fn_tenant_predicate(tenant_id) ON dbo.<table> AFTER INSERT;
 */
