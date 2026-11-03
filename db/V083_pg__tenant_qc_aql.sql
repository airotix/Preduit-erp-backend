BEGIN;

-- Company-wide AQL level for new quality inspections. Inspections already
-- recorded keep the level they were created with.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS qc_aql varchar(4) NOT NULL DEFAULT '2.5';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_tenants_qc_aql') THEN
        ALTER TABLE tenants ADD CONSTRAINT ck_tenants_qc_aql
            CHECK (qc_aql IN ('1.0', '1.5', '2.5', '4.0', '6.5'));
    END IF;
END $$;

COMMIT;
