/* ============================================================================
 * Preduit ERP - fix_auth.sql
 * Fixes "Login failed for user 'erp_app' (18456)".
 *
 * Run in SSMS connected with WINDOWS AUTHENTICATION (as an admin), then
 * RESTART the SQL Server service (see note at bottom). The login-mode change
 * only takes effect after a restart.
 * ==========================================================================*/
USE [master];
GO

/* 1. Enable Mixed Mode (SQL + Windows auth). LoginMode = 2 means "both".
      Requires a service restart to take effect. */
EXEC xp_instance_regwrite
    N'HKEY_LOCAL_MACHINE',
    N'Software\Microsoft\MSSQLServer\MSSQLServer',
    N'LoginMode', REG_DWORD, 2;
GO

/* 2. Make sure both logins exist, are enabled, and have the expected passwords
      (must match backend\.env). CHECK_POLICY=OFF skips Windows complexity rules. */
IF SUSER_ID('erp_system') IS NULL
    CREATE LOGIN erp_system WITH PASSWORD = 'ChangeMe_System#2026', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN erp_system WITH PASSWORD = 'ChangeMe_System#2026', CHECK_POLICY = OFF;
ALTER LOGIN erp_system ENABLE;
GO

IF SUSER_ID('erp_app') IS NULL
    CREATE LOGIN erp_app WITH PASSWORD = 'ChangeMe_App#2026', CHECK_POLICY = OFF;
ELSE
    ALTER LOGIN erp_app WITH PASSWORD = 'ChangeMe_App#2026', CHECK_POLICY = OFF;
ALTER LOGIN erp_app ENABLE;
GO

/* 3. Ensure the database users exist and are mapped (harmless if already done). */
USE [Preduit-ERP];
GO
IF USER_ID('erp_system') IS NULL CREATE USER erp_system FOR LOGIN erp_system;
IF USER_ID('erp_app')    IS NULL CREATE USER erp_app    FOR LOGIN erp_app;
GO
ALTER ROLE db_owner      ADD MEMBER erp_system;
ALTER ROLE db_datareader ADD MEMBER erp_app;
ALTER ROLE db_datawriter ADD MEMBER erp_app;
GO

PRINT 'Auth fixed. NOW RESTART the SQL Server service for Mixed Mode to apply.';
GO

/* ----------------------------------------------------------------------------
 * RESTART THE SERVICE (PowerShell as admin) - pick the one that matches:
 *     Restart-Service -Name 'MSSQLSERVER' -Force        # default instance
 *     Restart-Service -Name 'MSSQL$SQLEXPRESS' -Force   # SQL Express instance
 * Or use SSMS: right-click the server -> Restart.
 * -------------------------------------------------------------------------- */
