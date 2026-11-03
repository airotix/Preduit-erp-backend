/* PostgreSQL port of ../seed_dev_admin.sql (DEV ONLY).
 * Demo users (no password: they are invite-style placeholders), canonical roles
 * + scopes and approval rules. Re-runnable. */
SELECT set_config('seed.tenant', :'tenant', false);
DO $$
DECLARE t uuid := current_setting('seed.tenant')::uuid;
BEGIN
  INSERT INTO users (tenant_id, external_id, email, display_name, is_owner, status, role, department, last_active)
  SELECT t, x.ext, x.email, x.name, x.owner, x.status, x.role, x.dept, x.seen
    FROM (VALUES
      ('demo:amelia','amelia.k@systemsapparel.com','Amelia King', false,'Active', 'Admin',                'Operations','2 min ago'),
      ('demo:daniel','daniel.r@systemsapparel.com','Daniel Roth', false,'Active', 'Merchandiser',         'Catalog',   '1 hr ago'),
      ('demo:sofia', 'sofia.m@systemsapparel.com', 'Sofia Marino',false,'Active', 'Logistics / Inventory','Inventory', 'Yesterday'),
      ('demo:omar',  'omar.f@systemsapparel.com',  'Omar Farooq', false,'Invited','Manager',              'Procurement','3 days ago'),
      ('demo:nadia', 'nadia.h@systemsapparel.com', 'Nadia Hassan',false,'Active', 'Accountant',           'Finance',   '5 hr ago'),
      ('demo:leo',   'leo.v@systemsapparel.com',   'Leo Vance',   false,'Active', 'User Overview',        'Executive', '2 days ago')
    ) x(ext,email,name,owner,status,role,dept,seen)
   WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.tenant_id=t AND u.email=x.email);
  -- Adaptation: the original made Amelia the tenant owner; this tenant already has its own owner.

  DELETE FROM role_permissions WHERE tenant_id=t AND role_id IN (SELECT id FROM roles WHERE tenant_id=t
     AND name NOT IN ('Admin','Manager','Merchandiser','Accountant','User Overview','Logistics / Inventory'));
  DELETE FROM user_roles WHERE tenant_id=t AND role_id IN (SELECT id FROM roles WHERE tenant_id=t
     AND name NOT IN ('Admin','Manager','Merchandiser','Accountant','User Overview','Logistics / Inventory'));
  DELETE FROM roles WHERE tenant_id=t
     AND name NOT IN ('Admin','Manager','Merchandiser','Accountant','User Overview','Logistics / Inventory');

  INSERT INTO roles (tenant_id, name, description, is_system, scope)
  SELECT t, x.name, x.descr, true, x.scope
    FROM (VALUES
      ('Admin','Full access to the organization','Full access'),
      ('Manager','Operations oversight and approvals','Operations, Approvals'),
      ('Merchandiser','Catalog and sales','Catalog, Sales'),
      ('Accountant','Finance and accounting','Finance'),
      ('User Overview','Read-only dashboards and reports','Read-only'),
      ('Logistics / Inventory','Inventory, procurement and shipments','Inventory, Shipments')
    ) x(name,descr,scope)
   WHERE NOT EXISTS (SELECT 1 FROM roles r WHERE r.tenant_id=t AND r.name=x.name);
  UPDATE roles r SET scope = x.scope
    FROM (VALUES ('Admin','Full access'),('Manager','Operations, Approvals'),('Merchandiser','Catalog, Sales'),
                 ('Accountant','Finance'),('User Overview','Read-only'),('Logistics / Inventory','Inventory, Shipments')) x(name,scope)
   WHERE r.tenant_id=t AND r.name=x.name;

  IF NOT EXISTS (SELECT 1 FROM approval_rules WHERE tenant_id=t) THEN
    INSERT INTO approval_rules (tenant_id, name, condition, approver, status) VALUES
      (t,'High-value PO',          'PO total over €5,000',      'Manager','Active'),
      (t,'New supplier onboarding','Supplier not yet approved', 'Manager','Active'),
      (t,'Stock write-off',        'Adjustment over €1,000',    'Admin',  'Active'),
      (t,'Price override',         'Discount over 20%',         'Manager','Draft');
  END IF;
END $$;
