/* ============================================================================
 * Preduit ERP — V013__coa_detail_fields.sql
 * Richer Chart of Accounts fields a business owner needs to maintain finances.
 * Run after V012.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.chart_of_accounts ADD
    subtype         NVARCHAR(40)  NULL,
    description     NVARCHAR(300) NULL,
    currency_code   CHAR(3)       NOT NULL CONSTRAINT df_coa_ccy DEFAULT ('EUR'),
    opening_balance DECIMAL(19,4) NOT NULL CONSTRAINT df_coa_open DEFAULT (0),
    tax_rate        DECIMAL(5,2)  NULL,
    parent_code     NVARCHAR(20)  NULL,
    is_active       BIT           NOT NULL CONSTRAINT df_coa_active DEFAULT (1);
GO
