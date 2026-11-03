/* ============================================================================
 * Preduit ERP — setup.sql   (RUN THIS FIRST, once, in SSMS)
 *
 * This creates the database and the two "keys" (database users) the rest of the
 * scripts rely on. After this, run the migrations in order:
 *      setup.sql  ->  V001  ->  V002  ->  V003   (and optionally smoke_test_rls.sql)
 *
 * >>> BEFORE RUNNING: change the two passwords below. <<<
 * Make them match backend/.env  (SQL_SYSTEM_PASSWORD and SQL_APP_PASSWORD).
 *
 * This version is written for a LOCAL SQL Server via SSMS.
 * On Azure SQL, see the note at the bottom.
 * ==========================================================================*/

/* ---- 1. Create the two logins (server-level) ---------------------------- */
USE master;
GO

IF SUSER_ID('erp_system') IS NULL
    CREATE LOGIN erp_system WITH PASSWORD = 'ChangeMe_System#2026';
GO
IF SUSER_ID('erp_app') IS NULL
    CREATE LOGIN erp_app WITH PASSWORD = 'ChangeMe_App#2026';
GO

/* ---- 2. Create the database --------------------------------------------- */
IF DB_ID('preduit') IS NULL
    CREATE DATABASE preduit;
GO

USE preduit;
GO

/* ---- 3. Map the logins to users inside the database --------------------- */

/* erp_system = the "can do anything" key.
   Used to run the migrations and to create a new company (tenant) at signup.
   Our security policy (V002) lets this key see across all companies.        */
IF USER_ID('erp_system') IS NULL
    CREATE USER erp_system FOR LOGIN erp_system;
GO
ALTER ROLE db_owner ADD MEMBER erp_system;
GO

/* erp_app = the everyday key the running app uses.
   It can read and write data, but the security policy (V002) limits it to
   ONE company's rows at a time. It is deliberately NOT given the master key. */
IF USER_ID('erp_app') IS NULL
    CREATE USER erp_app FOR LOGIN erp_app;
GO
ALTER ROLE db_datareader ADD MEMBER erp_app;   -- can SELECT
ALTER ROLE db_datawriter ADD MEMBER erp_app;   -- can INSERT / UPDATE / DELETE
GO

PRINT 'Setup complete. Now run V001, then V002, then V003.';
GO

/* ============================================================================
 * NOTE FOR AZURE SQL DATABASE (skip if you are on a local SQL Server):
 *   - You cannot CREATE DATABASE or USE across databases in one script.
 *   - Create the "preduit" database in the Azure portal first.
 *   - Then connect directly to that database and run ONLY these two lines
 *     (Azure users are "contained", so no separate login step is needed):
 *
 *       CREATE USER erp_system WITH PASSWORD = 'ChangeMe_System#2026';
 *       ALTER ROLE db_owner ADD MEMBER erp_system;
 *       CREATE USER erp_app    WITH PASSWORD = 'ChangeMe_App#2026';
 *       ALTER ROLE db_datareader ADD MEMBER erp_app;
 *       ALTER ROLE db_datawriter ADD MEMBER erp_app;
 * ==========================================================================*/
