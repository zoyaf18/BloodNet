param(
    [string]$ProjectId = "project-bae56d7f-3ee2-48fc-bdd",
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path

function Get-DatabaseUrl {
    $db = $null

    try {
        $db = (gcloud secrets versions access latest --secret=bloodnet-database-url --project=$ProjectId 2>$null | Out-String).Trim()
    } catch {
        $db = $null
    }

    if (-not $db) {
        $tfvars = Join-Path $repo "infra\terraform\terraform.tfvars"
        if (Test-Path $tfvars) {
            $txt = Get-Content $tfvars -Raw
            $m = [regex]::Match($txt, '(?is)(?:BLOODNET_DATABASE_URL|bloodnet_database_url)\s*=\s*["'']?(?<v>[^"''\r\n]+)')
            if ($m.Success) {
                $db = $m.Groups['v'].Value.Trim()
            }
        }
    }

    if (-not $db) {
        $db = "postgresql://bloodnet:bloodnet_local@localhost:5432/bloodnet"
    }

    return $db
}

$db = Get-DatabaseUrl

Write-Host "Using database URL: $db" -ForegroundColor Cyan

if (-not $SkipBackend) {
    Start-Process powershell -ArgumentList @(
        '-NoExit',
        '-Command',
        "Set-Location '$repo'; `$env:BLOODNET_DATABASE_URL = '$db'; `$env:BLOODNET_ENV = 'local'; `$env:BLOODNET_AUTH_MODE = 'identity-platform'; .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8080"
    )
    Write-Host "Started backend on http://127.0.0.1:8080" -ForegroundColor Green
}

if (-not $SkipFrontend) {
    Start-Process powershell -ArgumentList @(
        '-NoExit',
        '-Command',
        "Set-Location '$repo\web\app'; `$env:VITE_BLOODNET_API_BASE_URL = ''; npm install; npm run dev -- --host 0.0.0.0"
    )
    Write-Host "Started frontend on http://localhost:5173" -ForegroundColor Green
}

Write-Host "" 
Write-Host "Open: http://localhost:5173" -ForegroundColor Yellow
