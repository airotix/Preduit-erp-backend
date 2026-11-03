/* ============================================================================
 * Preduit ERP — V049__invoice_order_link_and_backfill.sql
 *  1) Add invoices.order_no so an AR receivable can reference its sales order.
 *  2) One-time backfill: create an AR invoice (receivable) for every generated
 *     invoice DOCUMENT (dbo.sales_invoices) that doesn't have one yet, so those
 *     documents finally land on the customer ledger. customer_id is resolved by
 *     name within the tenant; posted = 0 so the GL sync posts them on next load.
 *
 * The backfill spans every tenant, so the tenant RLS policy is toggled OFF for
 * the set-based INSERT (its BLOCK predicate would otherwise reject cross-tenant
 * rows) and switched back ON immediately after. Idempotent: re-running inserts
 * nothing new (guarded by NOT EXISTS on the computed invoice_no). Run after V048.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

/* 1. Link column: which sales order this receivable came from. */
IF COL_LENGTH('dbo.invoices', 'order_no') IS NULL
    ALTER TABLE dbo.invoices ADD order_no NVARCHAR(32) NULL;
GO

/* 2. Backfill AR invoices from generated invoice documents (all tenants). */
ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = OFF);
GO

INSERT INTO dbo.invoices (tenant_id, invoice_no, order_no, customer_id, customer_name,
                          issued_date, amount, currency_code, status, posted, is_deleted)
SELECT
    si.tenant_id,
    COALESCE(si.invoice_no, CONCAT('SI-', si.id)),
    si.order_no,
    c.id,
    si.customer_name,
    CAST(COALESCE(si.created_at, SYSUTCDATETIME()) AS DATE),
    si.total,
    ISNULL(si.currency_code, 'EUR'),
    'Open',
    0,
    0
FROM dbo.sales_invoices si
LEFT JOIN dbo.customers c
       ON c.tenant_id = si.tenant_id
      AND c.name = si.customer_name
      AND c.is_deleted = 0
WHERE si.is_deleted = 0
  AND ISNULL(si.total, 0) > 0
  AND NOT EXISTS (
        SELECT 1 FROM dbo.invoices inv
        WHERE inv.tenant_id = si.tenant_id
          AND inv.invoice_no = COALESCE(si.invoice_no, CONCAT('SI-', si.id))
  );
GO

ALTER SECURITY POLICY dbo.TenantSecurityPolicy WITH (STATE = ON);
GO

PRINT 'V049: invoices.order_no added; AR receivables backfilled from sales_invoices documents.';
GO
