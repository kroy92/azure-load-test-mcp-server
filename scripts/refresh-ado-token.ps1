#requires -Version 7.0
<#
.SYNOPSIS
    Mints a corp-tenant Entra bearer token for the ADO Remote MCP Server and
    stores it in Key Vault so Azure Load Testing engines can use it.

.DESCRIPTION
    The ADO Remote MCP Server requires Microsoft Entra ID OAuth (no PAT). ALT
    engines run unattended in the non-prod tenant and cannot mint corp tokens
    on their own. This script:

      1. Discovers the required scope via RFC 9728 metadata.
      2. Acquires a token from the corp tenant using the cached `az login`.
      3. Pushes it to Key Vault as `ado-mcp-token`.

    Run this just before `az load test-run create`. Token lives ~1 hour, so
    cloud runs must finish inside that window.

.EXAMPLE
    az login --tenant 72f988bf-86f1-41af-91ab-2d7cd011db47
    ./scripts/refresh-ado-token.ps1
    az load test-run create --test-id mcp-multi-cloud-smoke ...
#>
[CmdletBinding()]
param(
    [string] $AdoOrg = $(if ($env:ADO_ORG) { $env:ADO_ORG } else { 'krishnaroy' }),
    [string] $CorpTenantId = '72f988bf-86f1-41af-91ab-2d7cd011db47',
    [string] $KeyVaultName = 'kv-mcpload-bd97',
    [string] $SecretName = 'ado-mcp-token'
)

$ErrorActionPreference = 'Stop'

Write-Host "Discovering ADO MCP scope for org '$AdoOrg'..." -ForegroundColor Cyan
$metaUrl = "https://mcp.dev.azure.com/.well-known/oauth-protected-resource/$AdoOrg"
$meta = Invoke-RestMethod -Uri $metaUrl -Method Get
$scope = $meta.scopes_supported[0]
Write-Host "  scope: $scope"

Write-Host "Acquiring Entra token from corp tenant $CorpTenantId..." -ForegroundColor Cyan
$tokenJson = az account get-access-token --tenant $CorpTenantId --scope $scope --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "az account get-access-token failed. Run: az login --tenant $CorpTenantId"
}
$token = ($tokenJson | ConvertFrom-Json).accessToken
$tokenLen = $token.Length
Write-Host "  token acquired ($tokenLen chars)"

Write-Host "Pushing to Key Vault $KeyVaultName/$SecretName..." -ForegroundColor Cyan
$null = az keyvault secret set `
    --vault-name $KeyVaultName `
    --name $SecretName `
    --value $token `
    --output none
if ($LASTEXITCODE -ne 0) {
    throw "az keyvault secret set failed."
}

$expiry = (Get-Date).AddMinutes(55).ToString('HH:mm:ss')
Write-Host "Done. Token valid until ~$expiry. Trigger your ALT run now." -ForegroundColor Green
