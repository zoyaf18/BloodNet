[CmdletBinding()]
param(
    [string]$ProjectId = "project-bae56d7f-3ee2-48fc-bdd",
    [string]$Region = "asia-south1"
)

$ErrorActionPreference = "Stop"
$requiredVariables = @("TF_VAR_jwt_secret", "TF_VAR_database_password")
foreach ($name in $requiredVariables) {
    if (-not (Get-Item "Env:$name" -ErrorAction SilentlyContinue)) {
        throw "Set $name in this terminal before Terraform state reconciliation."
    }
}

Push-Location (Join-Path $PSScriptRoot "..\infra\terraform")
try {
    terraform init -input=false
    $imports = @{
        'google_bigquery_table.demand_history' = "${ProjectId}/bloodnet/demand_history"
        'google_bigquery_table.forecast_results' = "${ProjectId}/bloodnet/forecast_results"
        'google_bigquery_table.supply_projection' = "${ProjectId}/bloodnet/supply_projection"
    }
    foreach ($address in $imports.Keys) {
        $inState = terraform state list | Select-String -SimpleMatch $address
        if (-not $inState) {
                    terraform import -input=false $address $imports[$address]
        } else {
            Write-Host "Already in state: $address"
        }
    }
    terraform fmt
    terraform validate
    Write-Host "Terraform state reconciliation complete. Review terraform plan before apply."
}
finally {
    Pop-Location
}
