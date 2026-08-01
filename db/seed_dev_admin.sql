/* ============================================================================
 * Preduit ERP — seed_dev_admin.sql  (DEV ONLY)
 * Demo users, role scopes, approval rules. Run after V020.
 * (Audit Log + Document Library populate from real activity/uploads.)
 * ==========================================================================*/
USE [Preduit-ERP];
GO
DECLARE @t UNIQUEIDENTIFIER = '33333333-3333-3333-3333-333333333333';
EXECUTE AS USER = 'erp_system';

/* A handful of users (external_id is a placeholder for demo invites) */
IF NOT EXISTS (SELECT 1 FROM dbo.users WHERE tenant_id=@t AND email='amelia.k@systemsapparel.com')
    INSERT INTO dbo.users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active) VALUES
      (@t, 'demo:amelia', 'amelia.k@systemsapparel.com', 'Amelia King',  1, 'Active',  'Administrator',  'Operations',   '2 min ago'),
      (@t, 'demo:daniel', 'daniel.r@systemsapparel.com', 'Daniel Roth',  0, 'Active',  'Merchandiser',   'Catalog',      '1 hr ago'),
      (@t, 'demo:sofia',  'sofia.m@systemsapparel.com',  'Sofia Marino', 0, 'Active',  'Warehouse Lead', 'Inventory',    'Yesterday'),
      (@t, 'demo:omar',   'omar.f@systemsapparel.com',   'Omar Farooq',  0, 'Invited', 'Buyer',          'Procurement',  '3 days ago');

/* Role scopes (roles were created at onboarding; set scope/desc for demo) */
UPDATE dbo.roles SET scope='Full access'          WHERE tenant_id=@t AND name='Owner';
IF NOT EXISTS (SELECT 1 FROM dbo.roles WHERE tenant_id=@t AND name='Administrator')
    INSERT INTO dbo.roles (tenant_id, name, description, is_system, scope) VALUES
      (@t, 'Administrator',  'Full access',            0, 'Full access'),
      (@t, 'Merchandiser',   'Catalog & sales',        0, 'Catalog, Sales'),
      (@t, 'Warehouse Lead', 'Inventory & shipments',  0, 'Inventory, Shipments'),
      (@t, 'Buyer',          'Procurement',            0, 'Procurement');

/* Approval rules */
IF NOT EXISTS (SELECT 1 FROM dbo.approval_rules WHERE tenant_id=@t)
    INSERT INTO dbo.approval_rules (tenant_id, name, [condition], approver, status) VALUES
      (@t, 'High-value PO',          'PO total over €5,000',      'Finance Manager',     'Active'),
      (@t, 'New supplier onboarding','Supplier not yet approved', 'Procurement Lead',    'Active'),
      (@t, 'Stock write-off',        'Adjustment over €1,000',    'Operations Director', 'Active'),
      (@t, 'Price override',         'Discount over 20%',         'Sales Manager',       'Draft');

REVERT;
GO
PRINT 'Admin demo data seeded.';
GO
