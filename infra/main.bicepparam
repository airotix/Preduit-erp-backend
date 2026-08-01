using './main.bicep'

param env = 'dev'
param prefix = 'preduit'
param sqlAdminLogin = 'preduitadmin'
// Do NOT commit a real password. Pass via CLI or CI secret:
//   az deployment group create ... -p sqlAdminPassword=$SQL_ADMIN_PASSWORD
param sqlAdminPassword = readEnvironmentVariable('SQL_ADMIN_PASSWORD', '')
param backendImage = 'preduit-backend:latest'
