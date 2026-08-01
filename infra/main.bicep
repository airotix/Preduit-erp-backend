/* ============================================================================
 * Preduit ERP — Phase 0 Azure infrastructure (Bicep)
 * Deploys the managed-PaaS footprint from docs/BACKEND_ARCHITECTURE_PLAN.md §8.
 *
 *   az deployment group create -g <rg> -f main.bicep -p main.bicepparam
 *
 * Front Door + WAF and Private Endpoints are intentionally left as a follow-up
 * (prod hardening); this template stands up a working dev/staging environment.
 * ==========================================================================*/

@description('Environment name: dev | staging | prod')
param env string = 'dev'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Short app prefix used in resource names')
param prefix string = 'preduit'

@description('SQL admin login (Entra admin is preferred for prod; SQL auth kept for bootstrap)')
param sqlAdminLogin string

@secure()
@description('SQL admin password')
param sqlAdminPassword string

@description('Container image for the FastAPI backend (ACR login server is prepended)')
param backendImage string = 'preduit-backend:latest'

var suffix = uniqueString(resourceGroup().id, env)
var names = {
  law: '${prefix}-law-${env}'
  appi: '${prefix}-appi-${env}'
  kv: take('${prefix}kv${env}${suffix}', 24)
  acr: take('${prefix}acr${env}${suffix}', 50)
  storage: take('${prefix}st${env}${suffix}', 24)
  redis: '${prefix}-redis-${env}'
  sqlServer: '${prefix}-sql-${env}-${suffix}'
  sqlPool: '${prefix}-pool-${env}'
  sqlDb: '${prefix}-db-${env}'
  acaEnv: '${prefix}-aca-${env}'
  backend: '${prefix}-backend-${env}'
}

/* --------------------------- Observability --------------------------- */
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.law
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: names.appi
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

/* --------------------------- Secrets & registry --------------------------- */
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: names.kv
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: names.acr
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

/* --------------------------- Storage (documents/photos) --------------------------- */
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.storage
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

/* --------------------------- Cache --------------------------- */
resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: names.redis
  location: location
  properties: {
    sku: { name: 'Basic', family: 'C', capacity: 0 }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

/* --------------------------- Azure SQL (Elastic Pool) --------------------------- */
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: names.sqlServer
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled' // tighten to Private Endpoint for prod
  }
}

resource sqlAllowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAllAzureIPs'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource sqlPool 'Microsoft.Sql/servers/elasticPools@2023-08-01-preview' = {
  parent: sqlServer
  name: names.sqlPool
  location: location
  sku: { name: 'GP_Gen5', tier: 'GeneralPurpose', family: 'Gen5', capacity: 2 }
  properties: {
    perDatabaseSettings: { minCapacity: 0, maxCapacity: 2 }
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: names.sqlDb
  location: location
  properties: {
    elasticPoolId: sqlPool.id
    collation: 'SQL_Latin1_General_CP1_CI_AS'
  }
}

/* --------------------------- Container Apps --------------------------- */
resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: names.acaEnv
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: names.backend
  location: location
  identity: { type: 'SystemAssigned' } // used for Key Vault, ACR, SQL (Entra) access
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [
        { server: acr.properties.loginServer, identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: '${acr.properties.loginServer}/${backendImage}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'ENV', value: env }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
            { name: 'KEY_VAULT_URI', value: kv.properties.vaultUri }
            { name: 'SQL_SERVER_FQDN', value: sqlServer.properties.fullyQualifiedDomainName }
            { name: 'SQL_DATABASE', value: names.sqlDb }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

/* --------------------------- RBAC: let the backend pull images & read secrets --------------------------- */
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var kvSecretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, backend.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: acrPullRole
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, backend.id, 'kvsecrets')
  scope: kv
  properties: {
    roleDefinitionId: kvSecretsUser
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output backendFqdn string = backend.properties.configuration.ingress.fqdn
output acrLoginServer string = acr.properties.loginServer
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output keyVaultUri string = kv.properties.vaultUri
