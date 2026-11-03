/* ============================================================================
 * Preduit ERP — fix_users_in_correct_db.sql
 *
 * Your tables live in the database "Preduit-ERP", but the users were created
 * in a different empty database ("preduit"). This creates the two users in the
 * CORRECT database and verifies them.
 *
 * The server-level logins already exist, so we only add the database users.
 * ==========================================================================*/
USE [Preduit-ERP];
GO

IF USER_ID('erp_system') IS NULL
    CREATE USER erp_system FOR LOGIN erp_system;
GO
IF USER_ID('erp_app') IS NULL
    CREATE USER erp_app FOR LOGIN erp_app;
GO

ALTER ROLE db_owner      ADD MEMBER erp_system;   -- master key (migrations + signup)
ALTER ROLE db_datareader ADD MEMBER erp_app;      -- everyday key: read
ALTER ROLE db_datawriter ADD MEMBER erp_app;      -- everyday key: write
GO

/* Verify — MUST return two rows. */
SELECT name, type_desc, create_date
FROM sys.database_principals
WHERE name IN ('erp_system', 'erp_app');
GO

/* Optional: remove the stray empty database that setup.sql created.
   Only run this if you confirm "preduit" has no tables you care about.
     DROP DATABASE preduit;
*/
