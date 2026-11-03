/* ============================================================================
 * Preduit ERP — V045__company_profile.sql
 * Rich company profile (logo/cover, identity, industry, HQ, contact, socials,
 * legal) edited from the Company Profile page. Every field is its own typed
 * column on dbo.tenants — so it can be queried/joined and reused across the
 * app (HQ address on invoices, legal name on tax filings, contact on receipts).
 * name / country / city / tax_registration already exist (V044); this adds the
 * rest. Run after V044.
 * ==========================================================================*/
SET XACT_ABORT ON;
GO

ALTER TABLE dbo.tenants ADD
    about                 NVARCHAR(400) NULL,
    logo_doc_id           NVARCHAR(64)  NULL,   -- documents.public_id of the logo
    cover_doc_id          NVARCHAR(64)  NULL,   -- documents.public_id of the cover
    industry              NVARCHAR(60)  NULL,
    business_type         NVARCHAR(80)  NULL,
    sales_model           NVARCHAR(60)  NULL,
    founded               NVARCHAR(10)  NULL,
    street                NVARCHAR(200) NULL,
    [state]               NVARCHAR(80)  NULL,
    postal                NVARCHAR(20)  NULL,
    business_email        NVARCHAR(256) NULL,
    phone                 NVARCHAR(40)  NULL,
    support_line          NVARCHAR(40)  NULL,
    opening_hours         NVARCHAR(120) NULL,
    website               NVARCHAR(200) NULL,
    social_linkedin       NVARCHAR(120) NULL,
    social_instagram      NVARCHAR(120) NULL,
    social_facebook       NVARCHAR(120) NULL,
    social_x              NVARCHAR(120) NULL,
    legal_name            NVARCHAR(200) NULL,
    legal_same_as_company BIT           NOT NULL CONSTRAINT df_tenant_legalsame DEFAULT (0),
    registration_number   NVARCHAR(60)  NULL;
GO
PRINT 'tenants company-profile columns added.';
GO
