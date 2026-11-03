<#
=============================================================================
 Preduit ERP - db_setup.ps1
 One-shot database bootstrap for a fresh SQL Server (local dev).

 What it does, in order:
   1. Creates the two logins (erp_system, erp_app) in [master].
   2. Creates the [Preduit-ERP] database (correct name - avoids the
      lowercase "preduit" bug in setup.sql / create_users.sql).
   3. Maps both logins to users inside [Preduit-ERP] with the right roles.
   4. Runs every migration V001..Vnnn (in numeric order).
   5. Runs every seed_dev_*.sql (in dependency order) for full demo data.
   6. Verifies: prints the demo product list.

 HOW TO RUN (from a PowerShell window):
   cd "<repo>\backend\db"
   # Windows auth (you are a local admin / sysadmin) - simplest:
   .\db_setup.ps1
   # ...or SQL auth as sa:
   .\db_setup.ps1 -SaPassword "YourSaPassword"
   # ...custom server / instance:
   .\db_setup.ps1 -Server "localhost\SQLEXPRESS"

 IMPORTANT: the two passwords below MUST match backend\.env
 (SQL_SYSTEM_PASSWORD and SQL_APP_PASSWORD).
=============================================================================
#>

param(
    [string]$Server          = "localhost",
    [string]$Database        = "Preduit-ERP",
    [string]$SystemPassword  = "ChangeMe_System#2026",
    [string]$AppPassword     = "ChangeMe_App#2026",
    [string]$SaPassword      = "",       # if set, uses SQL auth as 'sa'; otherwise Windows auth
    [switch]$SchemaOnly                  # pass -SchemaOnly to skip seed data
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- locate sqlcmd ---------------------------------------------------------
$sqlcmdCmd = Get-Command sqlcmd -ErrorAction SilentlyContinue
$sqlcmd = if ($sqlcmdCmd) { $sqlcmdCmd.Source } else { $null }
if (-not $sqlcmd) {
    Write-Host "ERROR: sqlcmd not found on PATH." -ForegroundColor Red
    Write-Host "Install it: winget install Microsoft.Sqlcmd   (or it ships with SSMS / SQL Server tools)." -ForegroundColor Yellow
    exit 1
}

# ---- auth flags common to every call --------------------------------------
#  -C trusts the server's self-signed cert (ODBC 18 encrypts by default)
#  -b aborts the batch on the first SQL error so we never silently continue
$auth = if ($SaPassword) { @("-U","sa","-P",$SaPassword) } else { @("-E") }
$common = @("-S",$Server,"-C","-b") + $auth

function Invoke-SqlFile([string]$file, [string]$db) {
    $name = Split-Path -Leaf $file
    Write-Host ("  -> {0}" -f $name) -ForegroundColor Cyan
    & $sqlcmd @common "-d" $db "-i" $file
    if ($LASTEXITCODE -ne 0) { throw "FAILED on $name (sqlcmd exit $LASTEXITCODE)" }
}

Write-Host "=== Preduit ERP database setup ===" -ForegroundColor Green
Write-Host ("Server   : {0}" -f $Server)
Write-Host ("Database : {0}" -f $Database)
Write-Host ("Auth     : {0}" -f ($(if ($SaPassword) {"SQL login 'sa'"} else {"Windows (current user)"})))
Write-Host ""

# ---- Step 1-3: logins + database + users (built inline, correct DB name) ---
Write-Host "[1/4] Creating logins, database and users..." -ForegroundColor Green
$bootstrap = @"
USE [master];
GO
IF SUSER_ID('erp_system') IS NULL
    CREATE LOGIN erp_system WITH PASSWORD = '$SystemPassword', CHECK_POLICY = OFF;
GO
IF SUSER_ID('erp_app') IS NULL
    CREATE LOGIN erp_app WITH PASSWORD = '$AppPassword', CHECK_POLICY = OFF;
GO
IF DB_ID('$Database') IS NULL
    CREATE DATABASE [$Database];
GO
USE [$Database];
GO
IF USER_ID('erp_system') IS NULL CREATE USER erp_system FOR LOGIN erp_system;
GO
IF USER_ID('erp_app')    IS NULL CREATE USER erp_app    FOR LOGIN erp_app;
GO
ALTER ROLE db_owner      ADD MEMBER erp_system;
ALTER ROLE db_datareader ADD MEMBER erp_app;
ALTER ROLE db_datawriter ADD MEMBER erp_app;
GO
PRINT 'Logins, database and users ready.';
GO
"@
$bootstrapFile = Join-Path $env:TEMP "preduit_bootstrap.sql"
$bootstrap | Set-Content -Encoding UTF8 $bootstrapFile
& $sqlcmd @common "-d" "master" "-i" $bootstrapFile
if ($LASTEXITCODE -ne 0) { throw "Bootstrap step failed (sqlcmd exit $LASTEXITCODE)" }
Remove-Item $bootstrapFile -ErrorAction SilentlyContinue

# ---- Step 4: migrations V001..Vnnn in numeric order ------------------------
Write-Host "`n[2/4] Applying schema migrations..." -ForegroundColor Green
$migrations = Get-ChildItem -Path $here -Filter "V*.sql" | Sort-Object Name
if (-not $migrations) { throw "No V*.sql migrations found in $here" }
foreach ($m in $migrations) { Invoke-SqlFile $m.FullName $Database }

# ---- Step 5: seeds in dependency order -------------------------------------
if ($SchemaOnly) {
    Write-Host "`n[3/4] -SchemaOnly set: skipping demo data." -ForegroundColor Yellow
} else {
    Write-Host "`n[3/4] Loading demo data (seeds)..." -ForegroundColor Green
    $seeds = @(
        "seed_dev_data.sql",               # tenant + base catalog (MUST be first)
        "seed_dev_sizes.sql",
        "seed_dev_catalog_detail.sql",
        "seed_dev_sales.sql",
        "seed_dev_sales_detail.sql",
        "seed_dev_inventory.sql",
        "seed_dev_procurement.sql",
        "seed_dev_procurement_detail.sql",
        "seed_dev_finance.sql",
        "seed_dev_fx.sql",
        "seed_dev_finance_aging.sql",
        "seed_dev_gl.sql",
        "seed_dev_production.sql",
        "seed_dev_quality.sql",
        "seed_dev_shipments.sql",
        "seed_dev_admin.sql",
        "seed_dev_controls.sql",
        "seed_dev_finance_ledgers.sql",
        "seed_dev_bank.sql"
    )
    foreach ($s in $seeds) {
        $path = Join-Path $here $s
        if (Test-Path $path) { Invoke-SqlFile $path $Database }
        else { Write-Host ("  -- skipped (not found): {0}" -f $s) -ForegroundColor DarkYellow }
    }
}

# ---- Step 6: verify --------------------------------------------------------
Write-Host "`n[4/4] Verifying (demo tenant products)..." -ForegroundColor Green
$verify = "SET NOCOUNT ON; EXECUTE AS USER='erp_system'; SELECT title, status FROM dbo.products; REVERT;"
& $sqlcmd @common "-d" $Database "-Q" $verify

Write-Host "`n=== DONE. Database '$Database' is ready. ===" -ForegroundColor Green
Write-Host "Next: make sure backend\.env has SQL_DATABASE=$Database, the matching passwords," -ForegroundColor Green
Write-Host "and DEV_TENANT_ID=33333333-3333-3333-3333-333333333333" -ForegroundColor Green
