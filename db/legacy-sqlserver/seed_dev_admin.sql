/* ============================================================================
 * Preduit ERP — seed_dev_admin.sql  (DEV ONLY)
 * Demo users, canonical roles + scopes, approval rules. Run after V020.
 * (Audit Log + Document Library populate from real activity/uploads.)
 *
 * Canonical role set (the only roles the system uses):
 *   Admin · Manager · Merchandiser · Accountant · User Overview · Logistics / Inventory
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* --- Demo users (external_id is a placeholder for demo invites) ---------- */
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='amelia.k@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:amelia', 'amelia.k@systemsapparel.com', 'Amelia King',  1, 'Active',  'Admin',                 'Operations',   '2 min ago');
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='daniel.r@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:daniel', 'daniel.r@systemsapparel.com', 'Daniel Roth',  0, 'Active',  'Merchandiser',          'Catalog',      '1 hr ago');
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='sofia.m@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:sofia',  'sofia.m@systemsapparel.com',  'Sofia Marino', 0, 'Active',  'Logistics / Inventory', 'Inventory',    'Yesterday');
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='omar.f@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:omar',   'omar.f@systemsapparel.com',   'Omar Farooq',  0, 'Invited', 'Manager',               'Procurement',  '3 days ago');
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='nadia.h@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:nadia',  'nadia.h@systemsapparel.com',  'Nadia Hassan', 0, 'Active',  'Accountant',            'Finance',      '5 hr ago');
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='leo.v@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:leo',    'leo.v@systemsapparel.com',    'Leo Vance',    0, 'Active',  'User Overview',         'Executive',    '2 days ago');

/* Remap any pre-existing demo users to the canonical role set (idempotent). */
UPDATE dbo.users SET role='Admin'                 WHERE tenant_id=@t AND email='amelia.k@systemsapparel.com';
UPDATE dbo.users SET role='Merchandiser'          WHERE tenant_id=@t AND email='daniel.r@systemsapparel.com';
UPDATE dbo.users SET role='Logistics / Inventory' WHERE tenant_id=@t AND email='sofia.m@systemsapparel.com';
UPDATE dbo.users SET role='Manager'               WHERE tenant_id=@t AND email='omar.f@systemsapparel.com';

/* --- Canonical roles + scopes -------------------------------------------- */
/* Remove any legacy roles so the Roles tab shows only the canonical set.
   Clear their links first to respect FKs. */
DECLARE @stale TABLE (id BIGINT);
INSERT INTO @stale (id)
    SELECT id FROM dbo.roles
    WHERE tenant_id=@t
      AND name NOT IN ('Admin','Manager','Merchandiser','Accountant','User Overview','Logistics / Inventory');
DELETE FROM dbo.role_permissions WHERE tenant_id=@t AND role_id IN (SELECT id FROM @stale);
DELETE FROM dbo.user_roles       WHERE tenant_id=@t AND role_id IN (SELECT id FROM @stale);
DELETE FROM dbo.roles            WHERE tenant_id=@t AND id     IN (SELECT id FROM @stale);

/* Ensure each canonical role exists. */
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Admin')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'Admin', 'Full access to the organization', 1, 'Full access');
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Manager')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'Manager', 'Operations oversight and approvals', 1, 'Operations, Approvals');
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Merchandiser')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'Merchandiser', 'Catalog and sales', 1, 'Catalog, Sales');
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Accountant')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'Accountant', 'Finance and accounting', 1, 'Finance');
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='User Overview')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'User Overview', 'Read-only dashboards and reports', 1, 'Read-only');
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Logistics / Inventory')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES (@t, 'Logistics / Inventory', 'Inventory, procurement and shipments', 1, 'Inventory, Shipments');

/* Refresh scopes (idempotent — roles may have been created at onboarding). */
UPDATE dbo.roles SET scope='Full access'           WHERE tenant_id=@t AND name='Admin';
UPDATE dbo.roles SET scope='Operations, Approvals'  WHERE tenant_id=@t AND name='Manager';
UPDATE dbo.roles SET scope='Catalog, Sales'        WHERE tenant_id=@t AND name='Merchandiser';
UPDATE dbo.roles SET scope='Finance'               WHERE tenant_id=@t AND name='Accountant';
UPDATE dbo.roles SET scope='Read-only'             WHERE tenant_id=@t AND name='User Overview';
UPDATE dbo.roles SET scope='Inventory, Shipments'  WHERE tenant_id=@t AND name='Logistics / Inventory';

/* --- Approval rules ------------------------------------------------------ */
IF NOT EXISTS (SELECT 1 FROM dbo.approval_rules WHERE tenant_id=@t)
    INSERT INTO dbo.approval_rules (tenant_id, name, [condition], approver, status) VALUES
      (@t, 'High-value PO',          'PO total over €5,000',      'Manager', 'Active'),
      (@t, 'New supplier onboarding','Supplier not yet approved', 'Manager', 'Active'),
      (@t, 'Stock write-off',        'Adjustment over €1,000',    'Admin',   'Active'),
      (@t, 'Price override',         'Discount over 20%',         'Manager', 'Draft');

REVERT;
GO
PRINT 'Admin demo data seeded.';
GO
