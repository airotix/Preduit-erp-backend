/* ============================================================================
 * Preduit ERP — create_users.sql
 * Creates the two database users the app + smoke test need, then verifies them.
 *
 * HOW TO RUN:
 *   1. In the SSMS toolbar, make sure the database dropdown says "preduit".
 *   2. Run this whole script (F5).
 *   3. Check the results grid at the bottom shows TWO rows (erp_system, erp_app).
 *   4. Then re-run smoke_test_rls.sql.
 *
 * (CHECK_POLICY = OFF just skips Windows password rules — fine for a dev box.)
 * ==========================================================================*/

/* --- 1. Logins (server level, live in master) --- */
USE master;
GO
IF SUSER_ID('erp_system') IS NULL
    CREATE LOGIN erp_system WITH PASSWORD = 'ChangeMe_System#2026', CHECK_POLICY = OFF;
GO
IF SUSER_ID('erp_app') IS NULL
    CREATE LOGIN erp_app WITH PASSWORD = 'ChangeMe_App#2026', CHECK_POLICY = OFF;
GO

/* --- 2. Users (inside the preduit database) --- */
USE preduit;
GO
IF USER_ID('erp_system') IS NULL
    CREATE USER erp_system FOR LOGIN erp_system;
GO
IF USER_ID('erp_app') IS NULL
    CREATE USER erp_app FOR LOGIN erp_app;
GO

/* --- 3. Permissions --- */
ALTER ROLE db_owner      ADD MEMBER erp_system;   -- master key (migrations + signup)
ALTER ROLE db_datareader ADD MEMBER erp_app;      -- everyday key: read
ALTER ROLE db_datawriter ADD MEMBER erp_app;      -- everyday key: write
GO

/* --- 4. Verify: this MUST return two rows --- */
SELECT name, type_desc, create_date
FROM sys.database_principals
WHERE name IN ('erp_system', 'erp_app');
GO
