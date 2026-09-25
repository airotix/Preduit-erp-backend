/* ============================================================================
 * Preduit ERP — Consolidated PostgreSQL Schema
 * Converted from 62 T-SQL migrations (V001–V062) into a single idempotent DDL.
 *
 * Type mapping:
 *   NVARCHAR(n)        → VARCHAR(n)
 *   NVARCHAR(MAX)      → TEXT
 *   BIT                → BOOLEAN
 *   UNIQUEIDENTIFIER   → UUID
 *   BIGINT IDENTITY    → BIGINT GENERATED ALWAYS AS IDENTITY
 *   INT IDENTITY       → INT GENERATED ALWAYS AS IDENTITY
 *   DATETIME2          → TIMESTAMPTZ
 *   TINYINT            → SMALLINT
 *   ROWVERSION         → (dropped — optimistic locking handled by app/xmin)
 *   NEWSEQUENTIALID()  → gen_random_uuid()
 *   SYSUTCDATETIME()   → NOW()
 *
 * RLS:
 *   SQL Server SECURITY POLICY + fn_tenant_predicate
 *   → PostgreSQL ALTER TABLE … ENABLE ROW LEVEL SECURITY + CREATE POLICY
 *   Session var: SET app.tenant_id = '<uuid>'
 *   Bypass var:  SET app.rls_bypass = 'true'
 * ==========================================================================*/

BEGIN;

/* ------------------------------------------------------------------
 * 0. Custom GUC namespace for session variables
 * ------------------------------------------------------------------ */
DO $$ BEGIN
    PERFORM set_config('app.tenant_id', '', true);
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

/* ------------------------------------------------------------------
 * 1. Database roles
 * ------------------------------------------------------------------ */
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_app') THEN
        CREATE ROLE erp_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_system') THEN
        CREATE ROLE erp_system LOGIN BYPASSRLS;
    END IF;
END $$;

/* ------------------------------------------------------------------
 * 2. Global reference tables (not tenant-scoped, no RLS)
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS currencies (
    code           CHAR(3)      NOT NULL PRIMARY KEY,
    name           VARCHAR(64)  NOT NULL,
    symbol         VARCHAR(8),
    decimal_places SMALLINT     NOT NULL DEFAULT 2,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS permissions (
    id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        VARCHAR(80)  NOT NULL UNIQUE,
    description VARCHAR(200)
);

/* ------------------------------------------------------------------
 * 3. Tenants & subscriptions
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS tenants (
    id                    UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    name                  VARCHAR(200) NOT NULL,
    slug                  VARCHAR(80)  NOT NULL UNIQUE,
    base_currency_code    CHAR(3)      NOT NULL REFERENCES currencies(code),
    region                VARCHAR(40)  NOT NULL DEFAULT 'primary',
    status                VARCHAR(20)  NOT NULL DEFAULT 'Active',
    setup_complete        BOOLEAN      NOT NULL DEFAULT FALSE,
    country               VARCHAR(80),
    city                  VARCHAR(120),
    tax_registration      VARCHAR(60),
    about                 VARCHAR(400),
    logo_doc_id           VARCHAR(64),
    cover_doc_id          VARCHAR(64),
    industry              VARCHAR(60),
    business_type         VARCHAR(80),
    sales_model           VARCHAR(60),
    founded               VARCHAR(10),
    street                VARCHAR(200),
    state                 VARCHAR(80),
    postal                VARCHAR(20),
    business_email        VARCHAR(256),
    phone                 VARCHAR(40),
    support_line          VARCHAR(40),
    opening_hours         VARCHAR(120),
    website               VARCHAR(200),
    social_linkedin       VARCHAR(120),
    social_instagram      VARCHAR(120),
    social_facebook       VARCHAR(120),
    social_x              VARCHAR(120),
    legal_name            VARCHAR(200),
    legal_same_as_company BOOLEAN      NOT NULL DEFAULT FALSE,
    registration_number   VARCHAR(60),
    bank_name             VARCHAR(120),
    bank_account          VARCHAR(60),
    bank_iban             VARCHAR(60),
    bank_swift            VARCHAR(20),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by            BIGINT,
    updated_at            TIMESTAMPTZ,
    updated_by            BIGINT,
    is_deleted            BOOLEAN      NOT NULL DEFAULT FALSE,
    deleted_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id),
    plan          VARCHAR(40)  NOT NULL DEFAULT 'trial',
    status        VARCHAR(20)  NOT NULL DEFAULT 'trialing',
    seat_limit    INT          NOT NULL DEFAULT 5,
    trial_ends_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_subs_tenant ON subscriptions (tenant_id);

/* ------------------------------------------------------------------
 * 4. Users, roles, RBAC
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS users (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id         UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id         UUID         NOT NULL REFERENCES tenants(id),
    external_id       VARCHAR(128) NOT NULL UNIQUE,
    email             VARCHAR(256) NOT NULL,
    display_name      VARCHAR(200),
    is_owner          BOOLEAN      NOT NULL DEFAULT FALSE,
    status            VARCHAR(20)  NOT NULL DEFAULT 'Active',
    role              VARCHAR(60),
    department        VARCHAR(120),
    last_active       VARCHAR(40),
    password_hash     VARCHAR(255),
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    is_platform_admin BOOLEAN      NOT NULL DEFAULT FALSE,
    last_login        TIMESTAMPTZ,
    email_verified    BOOLEAN      NOT NULL DEFAULT FALSE,
    failed_logins     INT          NOT NULL DEFAULT 0,
    locked_until      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ,
    updated_by        BIGINT,
    is_deleted        BOOLEAN      NOT NULL DEFAULT FALSE,
    deleted_at        TIMESTAMPTZ,
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS ix_users_tenant ON users (tenant_id);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS roles (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    name        VARCHAR(80)  NOT NULL,
    description VARCHAR(200),
    is_system   BOOLEAN      NOT NULL DEFAULT FALSE,
    scope       VARCHAR(200),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_roles_tenant ON roles (tenant_id);

CREATE TABLE IF NOT EXISTS role_permissions (
    tenant_id     UUID    NOT NULL REFERENCES tenants(id),
    role_id       BIGINT  NOT NULL REFERENCES roles(id),
    permission_id INT     NOT NULL REFERENCES permissions(id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_roles (
    tenant_id UUID   NOT NULL REFERENCES tenants(id),
    user_id   BIGINT NOT NULL REFERENCES users(id),
    role_id   BIGINT NOT NULL REFERENCES roles(id),
    PRIMARY KEY (user_id, role_id)
);

/* ------------------------------------------------------------------
 * 5. Multi-currency (FX rates)
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS exchange_rates (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id  UUID         NOT NULL REFERENCES tenants(id),
    from_ccy   CHAR(3)      NOT NULL REFERENCES currencies(code),
    to_ccy     CHAR(3)      NOT NULL REFERENCES currencies(code),
    rate       DECIMAL(19,8) NOT NULL,
    valid_from DATE         NOT NULL,
    source     VARCHAR(40),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, from_ccy, to_ccy, valid_from)
);
CREATE INDEX IF NOT EXISTS ix_fx_lookup ON exchange_rates (tenant_id, from_ccy, to_ccy, valid_from DESC);

/* ------------------------------------------------------------------
 * 6. Audit log & system settings
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    actor_id    BIGINT,
    action      VARCHAR(80)  NOT NULL,
    entity_type VARCHAR(80),
    entity_id   VARCHAR(64),
    detail      TEXT,
    occurred_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_audit_tenant_time ON audit_log (tenant_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS system_settings (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id  UUID         NOT NULL REFERENCES tenants(id),
    key        VARCHAR(120) NOT NULL,
    value      TEXT,
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, key)
);

/* ------------------------------------------------------------------
 * 7. Catalog
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS categories (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    name      VARCHAR(120) NOT NULL,
    parent_id BIGINT       REFERENCES categories(id),
    is_active BOOLEAN      NOT NULL DEFAULT TRUE,
    UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_cat_tenant ON categories (tenant_id);

CREATE TABLE IF NOT EXISTS attribute_values (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id  UUID        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id  UUID        NOT NULL REFERENCES tenants(id),
    attr_type  VARCHAR(20) NOT NULL,
    value      VARCHAR(60) NOT NULL,
    code       VARCHAR(20) NOT NULL,
    hex        VARCHAR(9),
    sort_order INT         NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, attr_type, code)
);
CREATE INDEX IF NOT EXISTS ix_attr_tenant ON attribute_values (tenant_id);

CREATE TABLE IF NOT EXISTS products (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id    UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id    UUID         NOT NULL REFERENCES tenants(id),
    title        VARCHAR(200) NOT NULL,
    category_id  BIGINT       REFERENCES categories(id),
    season       VARCHAR(40),
    status       VARCHAR(20)  NOT NULL DEFAULT 'Draft',
    composition  VARCHAR(120),
    gauge        VARCHAR(40),
    care         VARCHAR(120),
    origin       VARCHAR(80),
    hs_code      VARCHAR(20),
    weight       VARCHAR(20),
    image_url    TEXT,
    fabric       VARCHAR(120),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by   BIGINT,
    updated_at   TIMESTAMPTZ,
    updated_by   BIGINT,
    is_deleted   BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_prod_tenant ON products (tenant_id, status);

CREATE TABLE IF NOT EXISTS product_variants (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id       UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id       UUID          NOT NULL REFERENCES tenants(id),
    product_id      BIGINT        NOT NULL REFERENCES products(id),
    sku             VARCHAR(64)   NOT NULL,
    color_id        BIGINT        REFERENCES attribute_values(id),
    size_id         BIGINT        REFERENCES attribute_values(id),
    barcode         VARCHAR(64),
    price           DECIMAL(19,4) NOT NULL,
    currency_code   CHAR(3)       NOT NULL REFERENCES currencies(code),
    status          VARCHAR(20)   NOT NULL DEFAULT 'Active',
    qty_on_hand     INT           NOT NULL DEFAULT 0,
    retail_price    DECIMAL(19,4),
    wholesale_price DECIMAL(19,4),
    online_price    DECIMAL(19,4),
    supplier_price  DECIMAL(19,4),
    UNIQUE (tenant_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_var_product ON product_variants (tenant_id, product_id);

/* ------------------------------------------------------------------
 * 8. Sales
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS customers (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id       UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id       UUID          NOT NULL REFERENCES tenants(id),
    name            VARCHAR(200)  NOT NULL,
    email           VARCHAR(256)  NOT NULL,
    type            VARCHAR(20)   NOT NULL DEFAULT 'Retail',
    region          VARCHAR(80),
    status          VARCHAR(20)   NOT NULL DEFAULT 'Active',
    phone           VARCHAR(40),
    address         VARCHAR(300),
    code            VARCHAR(20),
    terms           VARCHAR(20),
    opening_balance DECIMAL(19,4) NOT NULL DEFAULT 0,
    tax_id          VARCHAR(40),
    bank_name       VARCHAR(120),
    bank_account    VARCHAR(60),
    currency        VARCHAR(3),
    contact_title   VARCHAR(120),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by      BIGINT,
    updated_at      TIMESTAMPTZ,
    updated_by      BIGINT,
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_cust_tenant ON customers (tenant_id);

CREATE TABLE IF NOT EXISTS sales_orders (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    order_no      VARCHAR(32),
    customer_id   BIGINT        REFERENCES customers(id),
    customer_name VARCHAR(200)  NOT NULL,
    channel       VARCHAR(20)   NOT NULL DEFAULT 'Online',
    item_count    INT           NOT NULL DEFAULT 0,
    total         DECIMAL(19,4) NOT NULL DEFAULT 0,
    currency_code CHAR(3)       NOT NULL DEFAULT 'EUR',
    status        VARCHAR(20)   NOT NULL DEFAULT 'New',
    order_date    DATE          NOT NULL DEFAULT CURRENT_DATE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ord_tenant ON sales_orders (tenant_id, order_date DESC);

CREATE TABLE IF NOT EXISTS invoices (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    invoice_no    VARCHAR(32),
    customer_id   BIGINT        REFERENCES customers(id),
    customer_name VARCHAR(200)  NOT NULL,
    issued_date   DATE          NOT NULL DEFAULT CURRENT_DATE,
    due_date      VARCHAR(40),
    due_on        DATE,
    order_no      VARCHAR(32),
    memo          VARCHAR(400),
    amount        DECIMAL(19,4) NOT NULL DEFAULT 0,
    currency_code CHAR(3)       NOT NULL DEFAULT 'EUR',
    status        VARCHAR(20)   NOT NULL DEFAULT 'Open',
    gl_journal_id BIGINT,
    posted        BOOLEAN       NOT NULL DEFAULT FALSE,
    payment_type  VARCHAR(10)   CHECK (payment_type IN ('cash', 'bank')),
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_inv_tenant ON invoices (tenant_id, issued_date DESC);

CREATE TABLE IF NOT EXISTS sales_returns (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    rma_no        VARCHAR(32),
    order_ref     VARCHAR(40),
    customer_name VARCHAR(200)  NOT NULL,
    reason        VARCHAR(80),
    refund        DECIMAL(19,4) NOT NULL DEFAULT 0,
    currency_code CHAR(3)       NOT NULL DEFAULT 'EUR',
    status        VARCHAR(20)   NOT NULL DEFAULT 'Inspecting',
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ret_tenant ON sales_returns (tenant_id);

CREATE TABLE IF NOT EXISTS sales_order_lines (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID          NOT NULL REFERENCES tenants(id),
    order_id  BIGINT        NOT NULL REFERENCES sales_orders(id),
    sku       VARCHAR(64),
    name      VARCHAR(200)  NOT NULL,
    qty       INT           NOT NULL DEFAULT 0,
    price     DECIMAL(19,4) NOT NULL DEFAULT 0,
    line_total DECIMAL(19,4) NOT NULL DEFAULT 0,
    color     VARCHAR(60),
    size      VARCHAR(60)
);
CREATE INDEX IF NOT EXISTS ix_ol_order ON sales_order_lines (tenant_id, order_id);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id  UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id  UUID          NOT NULL REFERENCES tenants(id),
    invoice_id BIGINT        NOT NULL REFERENCES invoices(id),
    sku        VARCHAR(64),
    name       VARCHAR(200)  NOT NULL,
    qty        INT           NOT NULL DEFAULT 0,
    price      DECIMAL(19,4) NOT NULL DEFAULT 0,
    line_total DECIMAL(19,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_il_invoice ON invoice_lines (tenant_id, invoice_id);

CREATE TABLE IF NOT EXISTS sales_invoices (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    invoice_no    VARCHAR(40),
    order_no      VARCHAR(40),
    customer_name VARCHAR(200),
    invoice_type  VARCHAR(20)   NOT NULL DEFAULT 'Retail',
    currency_code CHAR(3),
    total         DECIMAL(19,4) NOT NULL DEFAULT 0,
    status        VARCHAR(20)   NOT NULL DEFAULT 'Draft',
    data          TEXT          NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_sinv_list ON sales_invoices (tenant_id, is_deleted, id DESC);

/* ------------------------------------------------------------------
 * 9. Inventory
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS locations (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    name      VARCHAR(120) NOT NULL,
    code      VARCHAR(40),
    type      VARCHAR(20)  NOT NULL DEFAULT 'Warehouse',
    region    VARCHAR(80),
    capacity  INT,
    is_deleted BOOLEAN     NOT NULL DEFAULT FALSE,
    UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_loc_tenant ON locations (tenant_id);

CREATE TABLE IF NOT EXISTS stock_levels (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID   NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID   NOT NULL REFERENCES tenants(id),
    variant_id  BIGINT NOT NULL REFERENCES product_variants(id),
    location_id BIGINT NOT NULL REFERENCES locations(id),
    on_hand     INT    NOT NULL DEFAULT 0,
    reserved    INT    NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, variant_id, location_id)
);
CREATE INDEX IF NOT EXISTS ix_stk_tenant ON stock_levels (tenant_id);

CREATE TABLE IF NOT EXISTS stock_transfers (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id        UUID        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id        UUID        NOT NULL REFERENCES tenants(id),
    transfer_no      VARCHAR(32),
    from_location_id BIGINT      REFERENCES locations(id),
    to_location_id   BIGINT      REFERENCES locations(id),
    units            INT         NOT NULL DEFAULT 0,
    status           VARCHAR(20) NOT NULL DEFAULT 'Draft',
    eta              VARCHAR(40),
    is_deleted       BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_trf_tenant ON stock_transfers (tenant_id);

CREATE TABLE IF NOT EXISTS stock_transfer_lines (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    transfer_id BIGINT       NOT NULL REFERENCES stock_transfers(id),
    name        VARCHAR(200) NOT NULL,
    color       VARCHAR(60),
    size        VARCHAR(60),
    sku         VARCHAR(64),
    qty         INT          NOT NULL DEFAULT 0,
    is_deleted  BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_stl_transfer ON stock_transfer_lines (tenant_id, transfer_id);

CREATE TABLE IF NOT EXISTS reorder_alerts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id),
    variant_id    BIGINT       REFERENCES product_variants(id),
    sku           VARCHAR(64)  NOT NULL,
    available     INT          NOT NULL DEFAULT 0,
    reorder_point INT          NOT NULL DEFAULT 0,
    suggested     INT          NOT NULL DEFAULT 0,
    supplier      VARCHAR(200),
    severity      VARCHAR(20)  NOT NULL DEFAULT 'Low',
    is_deleted    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_alr_tenant ON reorder_alerts (tenant_id);

/* ------------------------------------------------------------------
 * 10. Procurement
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS suppliers (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id           UUID          NOT NULL REFERENCES tenants(id),
    name                VARCHAR(200)  NOT NULL,
    region              VARCHAR(80),
    category            VARCHAR(120),
    country_code        VARCHAR(4),
    lead_time           VARCHAR(40),
    on_time_pct         INT           NOT NULL DEFAULT 0,
    defect_rate         DECIMAL(5,2),
    price_rating        DECIMAL(3,1),
    score               DECIMAL(3,1),
    status              VARCHAR(20)   NOT NULL DEFAULT 'New',
    email               VARCHAR(256),
    phone               VARCHAR(40),
    address             VARCHAR(300),
    code                VARCHAR(20),
    terms               VARCHAR(20),
    vat_number          VARCHAR(40),
    contact_person      VARCHAR(120),
    bank_details        VARCHAR(400),
    bank_name           VARCHAR(120),
    bank_account_title  VARCHAR(120),
    bank_account_number VARCHAR(60),
    bank_swift          VARCHAR(20),
    bank_iban           VARCHAR(60),
    opening_balance     DECIMAL(19,4) NOT NULL DEFAULT 0,
    is_deleted          BOOLEAN       NOT NULL DEFAULT FALSE,
    UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_sup_tenant ON suppliers (tenant_id);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id        UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id        UUID          NOT NULL REFERENCES tenants(id),
    po_no            VARCHAR(32),
    supplier_id      BIGINT        REFERENCES suppliers(id),
    supplier_name    VARCHAR(200)  NOT NULL,
    supplier_country VARCHAR(4),
    item_count       INT           NOT NULL DEFAULT 0,
    total            DECIMAL(19,4) NOT NULL DEFAULT 0,
    currency_code    CHAR(3)       NOT NULL DEFAULT 'EUR',
    expected         VARCHAR(40),
    status           VARCHAR(24)   NOT NULL DEFAULT 'Pending approval',
    is_deleted       BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_po_tenant ON purchase_orders (tenant_id);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id  UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id  UUID          NOT NULL REFERENCES tenants(id),
    po_id      BIGINT        NOT NULL REFERENCES purchase_orders(id),
    name       VARCHAR(200)  NOT NULL,
    color      VARCHAR(60),
    size       VARCHAR(60),
    sku        VARCHAR(64),
    qty        INT           NOT NULL DEFAULT 0,
    price      DECIMAL(19,4) NOT NULL DEFAULT 0,
    line_total DECIMAL(19,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pol_po ON purchase_order_lines (tenant_id, po_id);

CREATE TABLE IF NOT EXISTS goods_receipts (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id        UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id        UUID         NOT NULL REFERENCES tenants(id),
    grn_no           VARCHAR(32),
    po_ref           VARCHAR(32),
    supplier_name    VARCHAR(200) NOT NULL,
    supplier_country VARCHAR(4),
    line_count       INT          NOT NULL DEFAULT 0,
    received_count   INT          NOT NULL DEFAULT 0,
    status           VARCHAR(20)  NOT NULL DEFAULT 'Expected',
    received_date    VARCHAR(40),
    location         VARCHAR(120),
    is_deleted       BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_grn_tenant ON goods_receipts (tenant_id);

CREATE TABLE IF NOT EXISTS goods_receipt_lines (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    grn_id    BIGINT       NOT NULL REFERENCES goods_receipts(id),
    name      VARCHAR(200) NOT NULL,
    sku       VARCHAR(64),
    ordered   INT          NOT NULL DEFAULT 0,
    received  INT          NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_grnl_grn ON goods_receipt_lines (tenant_id, grn_id);

CREATE TABLE IF NOT EXISTS po_invoices (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    invoice_no    VARCHAR(40),
    po_no         VARCHAR(40),
    supplier_name VARCHAR(200),
    currency_code CHAR(3),
    total         DECIMAL(19,4) NOT NULL DEFAULT 0,
    status        VARCHAR(20)   NOT NULL DEFAULT 'Draft',
    data          TEXT          NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_poinv_list ON po_invoices (tenant_id, is_deleted, id DESC);

/* ------------------------------------------------------------------
 * 11. Finance
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS chart_of_accounts (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id       UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id       UUID          NOT NULL REFERENCES tenants(id),
    code            VARCHAR(20)   NOT NULL,
    name            VARCHAR(200)  NOT NULL,
    acct_type       VARCHAR(20)   NOT NULL,
    balance         DECIMAL(19,4) NOT NULL DEFAULT 0,
    subtype         VARCHAR(40),
    description     VARCHAR(300),
    currency_code   CHAR(3)       NOT NULL DEFAULT 'EUR',
    opening_balance DECIMAL(19,4) NOT NULL DEFAULT 0,
    tax_rate        DECIMAL(5,2),
    parent_code     VARCHAR(20),
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    normal_side     CHAR(1),
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE,
    UNIQUE (tenant_id, code)
);
CREATE INDEX IF NOT EXISTS ix_coa_tenant ON chart_of_accounts (tenant_id);

CREATE TABLE IF NOT EXISTS journal_entries (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id      UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id      UUID          NOT NULL REFERENCES tenants(id),
    entry_no       VARCHAR(32)   NOT NULL,
    entry_date     VARCHAR(40),
    memo           VARCHAR(300),
    total_debit    DECIMAL(19,4) NOT NULL DEFAULT 0,
    total_credit   DECIMAL(19,4) NOT NULL DEFAULT 0,
    status         VARCHAR(20)   NOT NULL DEFAULT 'Draft',
    source_note    TEXT,
    entry_on       DATE,
    posted_at      TIMESTAMPTZ,
    period_id      BIGINT,
    reversed_of_id BIGINT,
    is_deleted     BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_je_tenant ON journal_entries (tenant_id);

CREATE TABLE IF NOT EXISTS journal_lines (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID          NOT NULL REFERENCES tenants(id),
    entry_id    BIGINT        NOT NULL REFERENCES journal_entries(id),
    account     VARCHAR(160)  NOT NULL,
    description VARCHAR(200),
    debit       DECIMAL(19,4) NOT NULL DEFAULT 0,
    credit      DECIMAL(19,4) NOT NULL DEFAULT 0,
    account_id  BIGINT        REFERENCES chart_of_accounts(id)
);
CREATE INDEX IF NOT EXISTS ix_jl_entry ON journal_lines (tenant_id, entry_id);

CREATE TABLE IF NOT EXISTS payments (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    payment_no    VARCHAR(32),
    pay_date      VARCHAR(40),
    party         VARCHAR(200)  NOT NULL,
    allocated_to  VARCHAR(60),
    amount        DECIMAL(19,4) NOT NULL DEFAULT 0,
    pay_type      VARCHAR(20)   NOT NULL DEFAULT 'Receipt',
    status        VARCHAR(20)   NOT NULL DEFAULT 'Pending',
    method        VARCHAR(30),
    reference     VARCHAR(60),
    notes         VARCHAR(300),
    gl_journal_id BIGINT,
    posted        BOOLEAN       NOT NULL DEFAULT FALSE,
    party_type    VARCHAR(20),
    party_id      BIGINT,
    payment_type  VARCHAR(10)   CHECK (payment_type IN ('cash', 'bank')),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_pmt_tenant ON payments (tenant_id);

CREATE TABLE IF NOT EXISTS ar_aging (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    customer_name VARCHAR(200)  NOT NULL,
    region        VARCHAR(80),
    current_amt   DECIMAL(19,4) NOT NULL DEFAULT 0,
    b1_30         DECIMAL(19,4) NOT NULL DEFAULT 0,
    b31_60        DECIMAL(19,4) NOT NULL DEFAULT 0,
    b61_90        DECIMAL(19,4) NOT NULL DEFAULT 0,
    b90_plus      DECIMAL(19,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_arage_tenant ON ar_aging (tenant_id);

CREATE TABLE IF NOT EXISTS ap_aging (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    supplier_name VARCHAR(200)  NOT NULL,
    region        VARCHAR(80),
    current_amt   DECIMAL(19,4) NOT NULL DEFAULT 0,
    b1_30         DECIMAL(19,4) NOT NULL DEFAULT 0,
    b31_60        DECIMAL(19,4) NOT NULL DEFAULT 0,
    b61_90        DECIMAL(19,4) NOT NULL DEFAULT 0,
    b90_plus      DECIMAL(19,4) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_apage_tenant ON ap_aging (tenant_id);

CREATE TABLE IF NOT EXISTS supplier_bills (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    bill_no       VARCHAR(32),
    supplier_name VARCHAR(200)  NOT NULL,
    po_ref        VARCHAR(32),
    amount        DECIMAL(19,4) NOT NULL DEFAULT 0,
    due_on        DATE,
    status        VARCHAR(20)   NOT NULL DEFAULT 'Open',
    supplier_id   BIGINT,
    gl_journal_id BIGINT,
    posted        BOOLEAN       NOT NULL DEFAULT FALSE,
    issued_date   DATE,
    memo          VARCHAR(400),
    payment_type  VARCHAR(10)   CHECK (payment_type IN ('cash', 'bank')),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_bill_tenant ON supplier_bills (tenant_id);

CREATE TABLE IF NOT EXISTS credit_notes (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID          NOT NULL REFERENCES tenants(id),
    cn_no         VARCHAR(32),
    customer_id   BIGINT,
    customer_name VARCHAR(200)  NOT NULL,
    cn_date       DATE,
    amount        DECIMAL(19,4) NOT NULL DEFAULT 0,
    reason        VARCHAR(200),
    gl_journal_id BIGINT,
    posted        BOOLEAN       NOT NULL DEFAULT FALSE,
    payment_type  VARCHAR(10)   CHECK (payment_type IN ('cash', 'bank')),
    is_deleted    BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_cn_tenant ON credit_notes (tenant_id);

CREATE TABLE IF NOT EXISTS fiscal_periods (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id  UUID        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id  UUID        NOT NULL REFERENCES tenants(id),
    name       VARCHAR(40) NOT NULL,
    start_date DATE        NOT NULL,
    end_date   DATE        NOT NULL,
    status     VARCHAR(12) NOT NULL DEFAULT 'Open',
    is_deleted BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_fp_tenant ON fiscal_periods (tenant_id, start_date);

CREATE TABLE IF NOT EXISTS budget_lines (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id    UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id    UUID          NOT NULL REFERENCES tenants(id),
    fiscal_year  INT           NOT NULL,
    account_code VARCHAR(20)   NOT NULL,
    account_name VARCHAR(200),
    amount       DECIMAL(19,4) NOT NULL DEFAULT 0,
    is_deleted   BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_bl_tenant ON budget_lines (tenant_id, fiscal_year);

CREATE TABLE IF NOT EXISTS fixed_assets (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id       UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id       UUID          NOT NULL REFERENCES tenants(id),
    asset_no        VARCHAR(32),
    name            VARCHAR(200)  NOT NULL,
    category        VARCHAR(80),
    cost            DECIMAL(19,4) NOT NULL DEFAULT 0,
    salvage         DECIMAL(19,4) NOT NULL DEFAULT 0,
    life_months     INT           NOT NULL DEFAULT 36,
    in_service_date DATE,
    accumulated     DECIMAL(19,4) NOT NULL DEFAULT 0,
    status          VARCHAR(12)   NOT NULL DEFAULT 'Active',
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_fa_tenant ON fixed_assets (tenant_id);

CREATE TABLE IF NOT EXISTS bank_accounts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id),
    name          VARCHAR(120) NOT NULL,
    account_no    VARCHAR(40),
    gl_code       VARCHAR(20),
    currency_code CHAR(3)      NOT NULL DEFAULT 'EUR',
    is_deleted    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_bank_tenant ON bank_accounts (tenant_id);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id          UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id          UUID          NOT NULL REFERENCES tenants(id),
    bank_account_id    BIGINT        NOT NULL,
    txn_date           DATE,
    description        VARCHAR(200),
    amount             DECIMAL(19,4) NOT NULL DEFAULT 0,
    matched_payment_id BIGINT,
    status             VARCHAR(12)   NOT NULL DEFAULT 'Unmatched',
    is_deleted         BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_bt_acct ON bank_transactions (tenant_id, bank_account_id);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id    UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id    UUID          NOT NULL REFERENCES tenants(id),
    customer_id  BIGINT,
    supplier_id  BIGINT,
    entry_date   DATE,
    description  VARCHAR(400),
    debit        DECIMAL(19,4) NOT NULL DEFAULT 0,
    credit       DECIMAL(19,4) NOT NULL DEFAULT 0,
    payment_type VARCHAR(10)   CHECK (payment_type IN ('cash', 'bank')),
    is_deleted   BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_le_customer ON ledger_entries (tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS ix_le_supplier ON ledger_entries (tenant_id, supplier_id);

/* ------------------------------------------------------------------
 * 12. Documents
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS documents (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id    UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id    UUID         NOT NULL REFERENCES tenants(id),
    doc_id       VARCHAR(40)  NOT NULL,
    module       VARCHAR(40)  NOT NULL,
    entity_type  VARCHAR(60),
    entity_ref   VARCHAR(80),
    filename     VARCHAR(260) NOT NULL,
    content_type VARCHAR(120),
    size_bytes   BIGINT       NOT NULL DEFAULT 0,
    storage_path VARCHAR(400) NOT NULL,
    uploaded_by  BIGINT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted   BOOLEAN      NOT NULL DEFAULT FALSE,
    UNIQUE (tenant_id, doc_id)
);
CREATE INDEX IF NOT EXISTS ix_doc_lookup ON documents (tenant_id, module, entity_ref);

/* ------------------------------------------------------------------
 * 13. Production
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS production_orders (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id      UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id      UUID         NOT NULL REFERENCES tenants(id),
    order_no       VARCHAR(32),
    style          VARCHAR(200) NOT NULL,
    factory        VARCHAR(120),
    qty            INT          NOT NULL DEFAULT 0,
    stage          VARCHAR(20)  NOT NULL DEFAULT 'Trims',
    progress       INT          NOT NULL DEFAULT 0,
    sales_order_id BIGINT,
    is_deleted     BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_po2_tenant ON production_orders (tenant_id);

CREATE TABLE IF NOT EXISTS production_order_lines (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id           UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id           UUID         NOT NULL REFERENCES tenants(id),
    order_id            BIGINT       NOT NULL REFERENCES production_orders(id),
    sales_order_line_id BIGINT       REFERENCES sales_order_lines(id),
    sku                 VARCHAR(64),
    name                VARCHAR(200) NOT NULL,
    color               VARCHAR(60),
    size                VARCHAR(60),
    qty                 INT          NOT NULL DEFAULT 0,
    is_deleted          BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_pol_order ON production_order_lines (tenant_id, order_id);
CREATE INDEX IF NOT EXISTS ix_pol_sol ON production_order_lines (tenant_id, sales_order_line_id);

CREATE TABLE IF NOT EXISTS bill_of_materials (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id    UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id    UUID          NOT NULL REFERENCES tenants(id),
    component    VARCHAR(200)  NOT NULL,
    style        VARCHAR(200),
    material     VARCHAR(80),
    qty_per_unit VARCHAR(40),
    cost         DECIMAL(19,4) NOT NULL DEFAULT 0,
    is_deleted   BOOLEAN       NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_bom_tenant ON bill_of_materials (tenant_id);

CREATE TABLE IF NOT EXISTS production_stages (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id),
    order_id      BIGINT       REFERENCES production_orders(id),
    line_id       BIGINT       REFERENCES production_order_lines(id),
    seq           INT          NOT NULL,
    name          VARCHAR(40)  NOT NULL,
    duration_days INT          NOT NULL DEFAULT 0,
    status        VARCHAR(16)  NOT NULL DEFAULT 'Pending',
    start_on      DATE,
    end_on        DATE,
    worker        VARCHAR(120),
    notes         VARCHAR(400),
    is_deleted    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ps_order ON production_stages (tenant_id, order_id, seq);
CREATE INDEX IF NOT EXISTS ix_ps_line ON production_stages (tenant_id, line_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ps_line_seq ON production_stages (line_id, seq)
    WHERE line_id IS NOT NULL;

/* ------------------------------------------------------------------
 * 14. Quality
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS inspections (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id             UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id             UUID         NOT NULL REFERENCES tenants(id),
    inspection_no         VARCHAR(32),
    order_ref             VARCHAR(40),
    stage                 VARCHAR(20)  NOT NULL DEFAULT 'Final',
    aql                   VARCHAR(10),
    defect_count          INT          NOT NULL DEFAULT 0,
    result                VARCHAR(20)  NOT NULL DEFAULT 'Pending',
    inspector             VARCHAR(120),
    sku                   VARCHAR(64),
    product               VARCHAR(200),
    batch_lot             VARCHAR(60),
    inspection_type       VARCHAR(40),
    prod_qty              INT,
    sample_size           INT,
    max_defects           INT,
    inspection_date       DATE         DEFAULT CURRENT_DATE,
    started_at            TIMESTAMPTZ,
    finalized_at          TIMESTAMPTZ,
    disposition           VARCHAR(24),
    disposition_notes     VARCHAR(400),
    assigned_to           VARCHAR(120),
    due_date              DATE,
    parent_inspection_id  BIGINT,
    shipment_ref          VARCHAR(32),
    item                  VARCHAR(200),
    is_deleted            BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_insp_tenant ON inspections (tenant_id);

CREATE TABLE IF NOT EXISTS defect_types (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    name      VARCHAR(120) NOT NULL,
    category  VARCHAR(40),
    severity  VARCHAR(20),
    frequency INT          NOT NULL DEFAULT 0,
    is_deleted BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_deft_tenant ON defect_types (tenant_id);

CREATE TABLE IF NOT EXISTS inspection_checks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id     UUID         NOT NULL,
    inspection_id BIGINT       NOT NULL REFERENCES inspections(id),
    seq           INT          NOT NULL DEFAULT 0,
    criterion     VARCHAR(120) NOT NULL,
    requirement   VARCHAR(200),
    target_value  DECIMAL(19,4),
    tolerance     DECIMAL(19,4),
    actual        VARCHAR(120),
    result        VARCHAR(12)  NOT NULL DEFAULT 'Pending',
    notes         VARCHAR(400),
    is_deleted    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_inschk_ins ON inspection_checks (inspection_id, seq);

CREATE TABLE IF NOT EXISTS inspection_defects (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id      UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id      UUID         NOT NULL,
    inspection_id  BIGINT       NOT NULL REFERENCES inspections(id),
    defect_no      VARCHAR(32),
    defect_type_id BIGINT,
    defect_name    VARCHAR(120),
    category       VARCHAR(40),
    severity       VARCHAR(20),
    qty_affected   INT          NOT NULL DEFAULT 1,
    location       VARCHAR(120),
    description    VARCHAR(400),
    corrective     VARCHAR(400),
    image_doc_id   VARCHAR(64),
    is_deleted     BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_insdef_ins ON inspection_defects (inspection_id);

/* ------------------------------------------------------------------
 * 15. Shipments
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS shipments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    shipment_no VARCHAR(32),
    order_ref   VARCHAR(40),
    carrier     VARCHAR(120),
    destination VARCHAR(160),
    status      VARCHAR(24)  NOT NULL DEFAULT 'Label created',
    eta         VARCHAR(40),
    is_deleted  BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ship_tenant ON shipments (tenant_id);

CREATE TABLE IF NOT EXISTS carriers (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    name        VARCHAR(120) NOT NULL,
    service     VARCHAR(80),
    avg_transit VARCHAR(40),
    on_time_pct INT          NOT NULL DEFAULT 0,
    status      VARCHAR(20)  NOT NULL DEFAULT 'Active',
    is_deleted  BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_car_tenant ON carriers (tenant_id);

CREATE TABLE IF NOT EXISTS shipment_lines (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    shipment_id BIGINT       NOT NULL REFERENCES shipments(id),
    sku         VARCHAR(64),
    description VARCHAR(200) NOT NULL,
    qty         INT          NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sl_ship ON shipment_lines (tenant_id, shipment_id);

/* ------------------------------------------------------------------
 * 16. Admin
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS approval_rules (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    name      VARCHAR(200) NOT NULL,
    condition VARCHAR(300),
    approver  VARCHAR(200),
    status    VARCHAR(20)  NOT NULL DEFAULT 'Active',
    is_deleted BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_ar_tenant ON approval_rules (tenant_id);

/* ------------------------------------------------------------------
 * 17. AI Insights
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS ai_snapshot (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id UUID         NOT NULL REFERENCES tenants(id),
    kind      VARCHAR(40)  NOT NULL,
    scope     VARCHAR(200) NOT NULL DEFAULT '',
    data      TEXT         NOT NULL,
    synced_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_aisnap_key ON ai_snapshot (tenant_id, kind, scope);

CREATE TABLE IF NOT EXISTS ai_sync_state (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id      UUID        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id      UUID        NOT NULL REFERENCES tenants(id) UNIQUE,
    last_synced_at TIMESTAMPTZ,
    status         VARCHAR(20) NOT NULL DEFAULT 'idle',
    message        VARCHAR(400)
);

/* ------------------------------------------------------------------
 * 18. Auth flows
 * ------------------------------------------------------------------ */
CREATE TABLE IF NOT EXISTS email_verifications (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    user_id     BIGINT       NOT NULL REFERENCES users(id),
    code_hash   VARCHAR(128) NOT NULL,
    attempts    INT          NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ  NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_emailver_user ON email_verifications (user_id, consumed_at);

CREATE TABLE IF NOT EXISTS password_resets (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    user_id     BIGINT       NOT NULL REFERENCES users(id),
    token_hash  VARCHAR(128) NOT NULL,
    expires_at  TIMESTAMPTZ  NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_pwreset_token ON password_resets (token_hash);

CREATE TABLE IF NOT EXISTS invitations (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id   UUID         NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    email       VARCHAR(256) NOT NULL,
    role        VARCHAR(60)  NOT NULL,
    token_hash  VARCHAR(128) NOT NULL,
    invited_by  BIGINT       REFERENCES users(id),
    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
    expires_at  TIMESTAMPTZ  NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_invite_token ON invitations (token_hash);
CREATE INDEX IF NOT EXISTS ix_invite_tenant ON invitations (tenant_id, status);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jti         UUID         NOT NULL UNIQUE,
    tenant_id   UUID         NOT NULL REFERENCES tenants(id),
    user_id     BIGINT       NOT NULL REFERENCES users(id),
    expires_at  TIMESTAMPTZ  NOT NULL,
    revoked_at  TIMESTAMPTZ,
    replaced_by UUID,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_rtok_user ON refresh_tokens (user_id, revoked_at);

/* ==================================================================
 * 19. Row-Level Security (RLS)
 *
 * PostgreSQL native RLS replaces SQL Server's fn_tenant_predicate +
 * SECURITY POLICY. Each tenant-scoped table gets a policy that checks
 * current_setting('app.tenant_id'). The erp_system role has BYPASSRLS,
 * and the app can also SET app.rls_bypass = 'true' for cross-tenant
 * provisioning reads on the erp_app connection.
 * ================================================================== */

-- Helper: list of all tenant-scoped tables and their tenant column
-- tenants uses "id", everything else uses "tenant_id"

-- Enable RLS on tenants (matched on id)
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenants;
CREATE POLICY tenant_isolation ON tenants
    USING (
        id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.rls_bypass', true) = 'true'
    )
    WITH CHECK (
        id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.rls_bypass', true) = 'true'
    );

-- Macro: enable RLS on all tenant_id-scoped tables
DO $rls$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'subscriptions', 'users', 'roles', 'role_permissions', 'user_roles',
            'exchange_rates', 'audit_log', 'system_settings',
            'categories', 'attribute_values', 'products', 'product_variants',
            'customers', 'sales_orders', 'invoices', 'sales_returns',
            'sales_order_lines', 'invoice_lines', 'sales_invoices',
            'locations', 'stock_levels', 'stock_transfers', 'stock_transfer_lines',
            'reorder_alerts',
            'suppliers', 'purchase_orders', 'purchase_order_lines',
            'goods_receipts', 'goods_receipt_lines', 'po_invoices',
            'chart_of_accounts', 'journal_entries', 'journal_lines',
            'payments', 'ar_aging', 'ap_aging', 'supplier_bills', 'credit_notes',
            'fiscal_periods', 'budget_lines', 'fixed_assets',
            'bank_accounts', 'bank_transactions', 'ledger_entries',
            'documents',
            'production_orders', 'production_order_lines', 'bill_of_materials',
            'production_stages',
            'inspections', 'defect_types', 'inspection_checks', 'inspection_defects',
            'shipments', 'carriers', 'shipment_lines',
            'approval_rules',
            'ai_snapshot', 'ai_sync_state',
            'email_verifications', 'password_resets', 'invitations', 'refresh_tokens'
        ])
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I
                USING (
                    tenant_id = current_setting(''app.tenant_id'', true)::uuid
                    OR current_setting(''app.rls_bypass'', true) = ''true''
                )
                WITH CHECK (
                    tenant_id = current_setting(''app.tenant_id'', true)::uuid
                    OR current_setting(''app.rls_bypass'', true) = ''true''
                )',
            tbl
        );
    END LOOP;
END $rls$;

/* ------------------------------------------------------------------
 * 20. Grants
 * ------------------------------------------------------------------ */
DO $grants$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO erp_app', tbl);
        EXECUTE format('GRANT ALL ON %I TO erp_system', tbl);
    END LOOP;

    -- Grant USAGE on sequences so IDENTITY columns work
    FOR tbl IN
        SELECT sequence_name FROM information_schema.sequences
        WHERE sequence_schema = 'public'
    LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO erp_app', tbl);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %I TO erp_system', tbl);
    END LOOP;
END $grants$;

/* ------------------------------------------------------------------
 * 21. Seed data
 * ------------------------------------------------------------------ */
INSERT INTO currencies (code, name, symbol, decimal_places) VALUES
    ('USD', 'US Dollar',        '$',  2),
    ('EUR', 'Euro',             '€',  2),
    ('GBP', 'Pound Sterling',   '£',  2),
    ('PKR', 'Pakistani Rupee',  '₨',  2),
    ('AED', 'UAE Dirham',       'د.إ', 2)
ON CONFLICT (code) DO NOTHING;

INSERT INTO permissions (code, description) VALUES
    ('tenant.manage',   'Manage organization settings & billing'),
    ('user.manage',     'Invite and manage users & roles'),
    ('catalog.read',    'View catalog'),
    ('catalog.write',   'Create/edit catalog'),
    ('inventory.read',  'View inventory'),
    ('inventory.write', 'Adjust inventory'),
    ('sales.read',      'View sales'),
    ('sales.write',     'Create/edit sales'),
    ('finance.read',    'View finance'),
    ('finance.write',   'Post finance entries')
ON CONFLICT (code) DO NOTHING;

COMMIT;
