/* ============================================================================
 * Preduit ERP — V014__finance_payment_fields.sql
 * Extra payment fields a business owner records (method, reference, notes).
 * Journal entries already have the fields needed to edit. Run after V013.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.payments ADD
    method    NVARCHAR(30)  NULL,   -- Bank transfer | Card | Cash | Cheque
    reference NVARCHAR(60)  NULL,
    notes     NVARCHAR(300) NULL;
GO
