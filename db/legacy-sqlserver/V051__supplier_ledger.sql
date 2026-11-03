/* ============================================================================
 * Preduit ERP — V051__supplier_ledger.sql
 * Mirror the customer-ledger capability onto suppliers:
 *   1) ledger_entries.supplier_id — manual entries can target a supplier.
 *   2) supplier_bills.memo         — editable description for bill (payable) rows.
 * Idempotent. Run after V050.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

IF COL_LENGTH('dbo.ledger_entries', 'supplier_id') IS NULL
    ALTER TABLE dbo.ledger_entries ADD supplier_id BIGINT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_le_supplier'
               AND object_id = OBJECT_ID('dbo.ledger_entries'))
    CREATE INDEX ix_le_supplier ON dbo.ledger_entries (tenant_id, supplier_id);
GO

IF COL_LENGTH('dbo.supplier_bills', 'memo') IS NULL
    ALTER TABLE dbo.supplier_bills ADD memo NVARCHAR(400) NULL;
GO

PRINT 'V051: ledger_entries.supplier_id + supplier_bills.memo added.';
GO
