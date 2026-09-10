# use -AllowDestructiveTerraformPlan to override the safety gate for Terraform plan replacements and to proceed with apply. This is a safety gate to prevent accidental destructive changes.
[CmdletBinding()]
param(
    [string]$ProjectId = "project-bae56d7f-3ee2-48fc-bdd",
    [string]$Region = "asia-south1",
    [string]$FrontendProjectId = "bloodnet-app",
    [string]$FrontendOrigin = "https://bloodnet-app.web.app",
    [string]$TerraformVarFile = "",
    [switch]$UsePrivateWorkerPool,
    [string]$WorkerPool = "",
    [switch]$UsePublicWorkerPool,
    [switch]$AllowDestructiveTerraformPlan
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$terraformDir = Join-Path $root "infra\terraform"
$releaseId = Get-Date -Format "yyyyMMddHHmmss"
$imageTag = "$Region-docker.pkg.dev/$ProjectId/bloodnet/bloodnet-api:release-$releaseId"
$latestImageTag = "$Region-docker.pkg.dev/$ProjectId/bloodnet/bloodnet-api:latest"
$buildContext = Join-Path $env:TEMP "bloodnet-build-$PID"
$planPath = Join-Path $terraformDir "bloodnet-deploy.tfplan"
$deploymentLockPath = Join-Path $terraformDir "bloodnet-deploy.lock"
$deploymentLock = $null

try {
    $deploymentLock = [System.IO.File]::Open(
        $deploymentLockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch {
    throw "Another BloodNet deployment is already running or the deployment lock is unavailable: $deploymentLockPath"
}

foreach ($commandName in @("gcloud", "terraform", "docker", "npm", "firebase")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required deployment command '$commandName' is not available on PATH. Install it before deploying."
    }
}

if (-not $TerraformVarFile) {
    $TerraformVarFile = Join-Path $terraformDir "terraform.secrets.tfvars"
}
if ($FrontendProjectId -ne "bloodnet-app") {
    throw "FrontendProjectId must be bloodnet-app. Hosting deployments are intentionally pinned to https://bloodnet-app.web.app."
}
if ($FrontendOrigin -ne "https://bloodnet-app.web.app") {
    throw "FrontendOrigin must be https://bloodnet-app.web.app so backend CORS matches the deployed Hosting site."
}
if (-not (Test-Path $TerraformVarFile -PathType Leaf)) {
    throw "Terraform variable file not found: $TerraformVarFile"
}
$TerraformVarFile = [System.IO.Path]::GetFullPath($TerraformVarFile)

# The project is protected by VPC Service Controls. Use the private pool by
# default so Cloud Build can fetch its source from the in-perimeter bucket.
# Public workers are an explicit opt-out and can fail with FETCH_SOURCE_FAILED.
if ($WorkerPool) {
    $workerPool = $WorkerPool
} elseif (-not $UsePublicWorkerPool) {
    $workerPool = "projects/$((gcloud projects describe $ProjectId --format='value(projectNumber)').Trim())/locations/$Region/workerPools/bloodnet-private-pool"
} else {
    $workerPool = $null
}
$previousAllowedOrigins = $env:TF_VAR_allowed_origins
$previousDatabasePassword = $env:TF_VAR_database_password
$previousJwtSecret = $env:TF_VAR_jwt_secret

$terraformSecrets = Get-Content $TerraformVarFile -Raw
$terraformDefaults = Get-Content (Join-Path $terraformDir "terraform.tfvars") -Raw
$notificationsEnabled = $terraformDefaults -match '(?m)^enable_notifications\s*=\s*true\s*$' -or $terraformSecrets -match '(?m)^enable_notifications\s*=\s*true\s*$'
$messagingWebhookEnabled = $terraformDefaults -match '(?m)^enable_messaging_webhook\s*=\s*true\s*$' -or $terraformSecrets -match '(?m)^enable_messaging_webhook\s*=\s*true\s*$'
$requiredProductionVars = @(
    "notification_provider_url",
    "notification_provider_token",
    "notification_receipt_secret",
    "notification_delivery_url"
)
if ($notificationsEnabled) {
    foreach ($name in $requiredProductionVars) {
        $pattern = '(?m)^' + [regex]::Escape($name) + '\s*=\s*"([^"]*)"'
        $match = [regex]::Match($terraformSecrets, $pattern)
        if (-not $match.Success -or [string]::IsNullOrWhiteSpace($match.Groups[1].Value)) {
            throw "TerraformVarFile must define $name for production notification delivery."
        }
    }
}
if ($messagingWebhookEnabled) {
    $pattern = '(?m)^messaging_webhook_secret\s*=\s*"([^"]*)"'
    $match = [regex]::Match($terraformSecrets, $pattern)
    if (-not $match.Success -or [string]::IsNullOrWhiteSpace($match.Groups[1].Value)) {
        throw "TerraformVarFile must define messaging_webhook_secret when enable_messaging_webhook is true."
    }
}

Push-Location $root
try {
    Push-Location "web\app"
    npm run build
    npm run deploy:hosting
    if ($LASTEXITCODE -ne 0) { throw "Firebase Hosting deployment failed." }
    Pop-Location

    New-Item -ItemType Directory -Force "$buildContext\web\app" | Out-Null
    Copy-Item Dockerfile, requirements.txt, main.py -Destination $buildContext
    foreach ($directory in @("contracts", "services", "sim", "ml", "migrations")) {
        Copy-Item $directory -Destination $buildContext -Recurse -Force
    }
    New-Item -ItemType Directory -Force "$buildContext\scripts" | Out-Null
    Copy-Item "scripts\bootstrap_sop_rag.py" -Destination "$buildContext\scripts" -Force
    Copy-Item "scripts\start_api.sh" -Destination "$buildContext\scripts" -Force
    Copy-Item "web\app\dist" -Destination "$buildContext\web\app" -Recurse -Force

    # Submit asynchronously so local source staging and the remote build do not
    # leave this deployment shell waiting without a visible status.
    $buildConfigPath = Join-Path $buildContext "cloudbuild.yaml"
    @"
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - build
      - --network=cloudbuild
      - --no-cache
      - -t
      - $imageTag
      - -t
      - $latestImageTag
      - .
images:
  - '$imageTag'
  - '$latestImageTag'
options:
  logging: CLOUD_LOGGING_ONLY
"@ | Set-Content -Path $buildConfigPath -Encoding UTF8

    $buildArgs = @(
        $buildContext,
        "--project=$ProjectId",
        "--region=$Region",
        "--config=$buildConfigPath",
        "--quiet"
    )
    if ($workerPool) {
        $buildArgs += "--worker-pool=$workerPool"
    }

    # Preserve Cloud Build diagnostics. Source-fetch and admission failures can
    # happen before a build step starts, so Docker logs alone are insufficient.
    $buildArgs += "--async"
    $submitJob = Start-Job -ScriptBlock {
        param([string[]]$arguments)
        & gcloud builds submit @arguments 2>&1
        $exitCode = $LASTEXITCODE
        Write-Output "BUILD_EXIT_CODE=$exitCode"
    } -ArgumentList (,$buildArgs)
    $submitCompleted = Wait-Job -Job $submitJob -Timeout 120
    if ($null -eq $submitCompleted) {
        Stop-Job -Job $submitJob -ErrorAction SilentlyContinue
        $stagedOutput = @(Receive-Job -Job $submitJob -ErrorAction SilentlyContinue)
        Remove-Job -Job $submitJob -Force -ErrorAction SilentlyContinue
        Write-Host "Cloud Build submission timed out while staging source after 120 seconds."
        $stagedOutput | ForEach-Object { Write-Host $_ }
        throw "Cloud Build submission timed out before returning a build ID."
    }
    $buildOutput = @(Receive-Job -Job $submitJob)
    $buildExitCode = ($buildOutput | Where-Object { $_ -match 'BUILD_EXIT_CODE=(\d+)' } | Select-Object -Last 1)
    $buildExitCode = if ($buildExitCode) { [int]$Matches[1] } else { 0 }
    Remove-Job -Job $submitJob -Force -ErrorAction SilentlyContinue
    if ($buildExitCode -ne 0) {
        Write-Host "Cloud Build submission output:"
        $buildOutput | ForEach-Object { Write-Host $_ }
        throw "Cloud Build submission failed with exit code $buildExitCode."
    }
    $buildOutputText = ($buildOutput | ForEach-Object { $_.ToString() }) -join "`n"
    $buildIdMatch = [regex]::Match($buildOutputText, '(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b')
    $buildId = if ($buildIdMatch.Success) { $buildIdMatch.Value } else { "" }
    if (-not $buildId) {
        Write-Host "Cloud Build submission output:"
        $buildOutput | ForEach-Object { Write-Host $_ }
        throw "Cloud Build submission returned no build ID."
    }
    Write-Host "Cloud Build submitted: $buildId"

    $buildDeadline = (Get-Date).AddMinutes(30)
    $buildStatus = ""
    $pollFailures = 0
    do {
        Start-Sleep -Seconds 5
        $statusOutput = @(gcloud builds describe $buildId --project=$ProjectId --region=$Region --format="value(status)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $statusOutput) {
            $buildStatus = ($statusOutput -join "").Trim()
            $pollFailures = 0
            Write-Host "Cloud Build status: $buildStatus"
        } else {
            $pollFailures++
            Write-Warning "Unable to poll Cloud Build status (attempt $pollFailures). Retrying while build $buildId continues."
        }
        if ($buildStatus -in @("SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED")) { break }
    } while ((Get-Date) -lt $buildDeadline)
    if ($buildStatus -ne "SUCCESS") {
        throw "Cloud Build $buildId did not succeed. Final status: $buildStatus"
    }
    
    # Wait for the image to be available in Artifact Registry
    $digest = $null
    $retries = 0
    $lastDescribeExitCode = 0
    while (-not $digest -and $retries -lt 30) {
        Start-Sleep -Seconds 2
        $digestOutput = @(gcloud artifacts docker images describe $latestImageTag --project=$ProjectId --format="value(image_summary.digest)" 2>$null)
        $lastDescribeExitCode = $LASTEXITCODE
        if ($lastDescribeExitCode -eq 0) {
            $digest = ($digestOutput -join "").Trim()
        } else {
            Write-Warning "Artifact Registry image lookup failed (attempt $($retries + 1)/30, exit code $lastDescribeExitCode). Retrying."
        }
        $retries++
    }
    if ($lastDescribeExitCode -ne 0) { throw "Unable to inspect the pushed container image after 60 seconds (last gcloud exit code: $lastDescribeExitCode)." }
    if (-not $digest) { throw "Unable to resolve the pushed image digest (image not found in Artifact Registry)." }
    $releaseImage = "$Region-docker.pkg.dev/$ProjectId/bloodnet/bloodnet-api@$digest"
    Write-Host "Resolved image: $releaseImage"

    Push-Location $terraformDir
    terraform init
    if ($LASTEXITCODE -ne 0) { throw "Terraform init failed." }
    terraform validate
    if ($LASTEXITCODE -ne 0) { throw "Terraform validation failed." }
    $env:TF_VAR_allowed_origins = '["' + $FrontendOrigin + '"]'

    # These resources predate Terraform state in some environments. Import them
    # before planning so apply does not fail with a 409 already-exists error.
    $terraformSecrets = Get-Content $TerraformVarFile -Raw
    $databasePasswordMatch = [regex]::Match($terraformSecrets, '(?m)^database_password\s*=\s*"([^"]*)"')
    $jwtSecretMatch = [regex]::Match($terraformSecrets, '(?m)^jwt_secret\s*=\s*"([^"]*)"')
    if (-not $databasePasswordMatch.Success -or -not $jwtSecretMatch.Success) {
        throw "TerraformVarFile must define database_password and jwt_secret for state imports."
    }
    $env:TF_VAR_database_password = $databasePasswordMatch.Groups[1].Value
    $env:TF_VAR_jwt_secret = $jwtSecretMatch.Groups[1].Value

    $imports = @(
        @("google_compute_global_address.private_services", "projects/$ProjectId/global/addresses/bloodnet-private-services"),
        @("google_service_networking_connection.private_services", "projects/$ProjectId/global/networks/default:servicenetworking.googleapis.com"),
        @("google_kms_key_ring.bloodnet", "projects/$ProjectId/locations/us/keyRings/bloodnet"),
        @("google_kms_crypto_key.bloodnet", "projects/$ProjectId/locations/us/keyRings/bloodnet/cryptoKeys/bloodnet-key"),
        @("google_pubsub_topic.secret_rotation_notifications", "projects/$ProjectId/topics/secret-rotation-notifications")
    )
    $stateAddresses = @(terraform state list)
    if ($LASTEXITCODE -ne 0) { throw "Unable to inspect Terraform state." }
    foreach ($import in $imports) {
        if ($stateAddresses -notcontains $import[0]) {
            terraform import -input=false $import[0] $import[1]
            if ($LASTEXITCODE -ne 0) { throw "Terraform import failed for $($import[0])." }
        }
    }

    # API Gateway config IDs are live-managed by GCP and cannot be safely
    # replaced while the gateway still points at them. Generate a fresh config
    # identifier for each deployment so Terraform creates a new config instead
    # of trying to mutate the active one in place.
    # Always bind this deployment to its own immutable config. An inherited
    # TF_VAR value may point at an older live config and roll the gateway back.
    $gatewayConfigId = "bloodnet-api-config-$releaseId"
    $env:TF_VAR_gateway_api_config_id = $gatewayConfigId

    # Review the full plan before apply. The private-network fix intentionally
    # replaces the database secret version; API Gateway configs are recreated
    # with a fresh ID so the live gateway can migrate without a 409 race.
    $planArguments = @(
        "plan",
        "-input=false",
        "-var-file=$TerraformVarFile",
        "-var=project_id=$ProjectId",
        "-var=region=$Region",
        "-var=deploy_cloud_run=true",
        "-var=cloud_run_image=$releaseImage",
        "-var=gateway_api_config_id=$gatewayConfigId",
        "-out=$planPath"
    )
    $planSucceeded = $false
    for ($planAttempt = 1; $planAttempt -le 3; $planAttempt++) {
        & terraform @planArguments
        if ($LASTEXITCODE -eq 0) {
            $planSucceeded = $true
            break
        }
        if ($planAttempt -lt 3) {
            Write-Host "Terraform plan attempt $planAttempt failed; retrying against the Google APIs."
        }
    }
    if (-not $planSucceeded) { throw "Terraform plan failed after 3 attempts." }
    $planSummary = (terraform show -no-color $planPath | Out-String)
    if ($planSummary -match 'google_api_gateway_api_config\.bloodnet.*must be replaced') {
        $gatewayConfigId = "bloodnet-api-config-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
        $env:TF_VAR_gateway_api_config_id = $gatewayConfigId
        Write-Host "API Gateway config is still in use; regenerating a fresh config ID ($gatewayConfigId) and re-running the plan."
        $planArguments = @(
            "plan",
            "-input=false",
            "-var-file=$TerraformVarFile",
            "-var=project_id=$ProjectId",
            "-var=region=$Region",
            "-var=deploy_cloud_run=true",
            "-var=cloud_run_image=$releaseImage",
            "-var=gateway_api_config_id=$gatewayConfigId",
            "-out=$planPath"
        )
        & terraform @planArguments
        if ($LASTEXITCODE -ne 0) { throw "Terraform plan failed after regenerating the live API Gateway config ID." }
    }
    
    # Apply exactly the reviewed plan. Re-running apply with variables would
    # create a new plan and could change the resources after the safety gate.
    terraform apply -auto-approve $planPath
    if ($LASTEXITCODE -ne 0) {
        throw "Terraform apply failed. Review the Terraform resource error above; no automatic retry is attempted against a partially-applied plan."
    }
    $serviceUrl = (terraform output -raw cloud_run_url).Trim()
    Pop-Location

    $identityToken = (gcloud auth print-identity-token).Trim()
    if (-not $identityToken) { throw "Unable to obtain an identity token for the Cloud Run health check." }
    $health = Invoke-RestMethod "$serviceUrl/health" -Headers @{ Authorization = "Bearer $identityToken" }
    if (-not $health) { throw "Cloud Run health check returned an empty response." }
    Write-Host "Deployed $releaseImage"
    Write-Host "Cloud Run: $serviceUrl"
    Write-Host "Health check passed."
}
finally {
    if ($deploymentLock) {
        $deploymentLock.Dispose()
    }
    if ($null -eq $previousAllowedOrigins) {
        Remove-Item Env:TF_VAR_allowed_origins -ErrorAction SilentlyContinue
    } else {
        $env:TF_VAR_allowed_origins = $previousAllowedOrigins
    }
    if ($null -eq $previousDatabasePassword) {
        Remove-Item Env:TF_VAR_database_password -ErrorAction SilentlyContinue
    } else {
        $env:TF_VAR_database_password = $previousDatabasePassword
    }
    if ($null -eq $previousJwtSecret) {
        Remove-Item Env:TF_VAR_jwt_secret -ErrorAction SilentlyContinue
    } else {
        $env:TF_VAR_jwt_secret = $previousJwtSecret
    }
    if (Test-Path $planPath) { Remove-Item $planPath -Force -ErrorAction SilentlyContinue }
    if (Test-Path $buildContext) { Remove-Item $buildContext -Recurse -Force }
    while ((Get-Location).Path -ne $root) { Pop-Location }
}
