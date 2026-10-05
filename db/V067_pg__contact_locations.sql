-- Preserve legacy regions/addresses; new optional geography is stored separately.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS country_code VARCHAR(4);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS state VARCHAR(120);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS city VARCHAR(120);
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS state VARCHAR(120);
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS city VARCHAR(120);
