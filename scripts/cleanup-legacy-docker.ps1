# Remove legacy ipa / intelligent_ai compose projects, containers, and images.
# Usage:
#   .\scripts\cleanup-legacy-docker.ps1
#   .\scripts\cleanup-legacy-docker.ps1 -Apply
#   .\scripts\cleanup-legacy-docker.ps1 -Apply -RemoveImages
param(
    [switch]$Apply,
    [switch]$RemoveImages
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $Root "docker-compose.yml"))) {
    throw "docker-compose.yml not found under $Root"
}

Set-Location $Root

$legacyProjects = @('ipa_dev', 'ipa_prod', 'intellrag_dev', 'intellrag_prod')
$legacyImageRepos = @(
    'ipa_backend',
    'ipa_elasticsearch',
    'ipa_frontend',
    'intelligent_ai_elasticsearch',
    'intelligent_ai_backend',
    'intelligent_ai_frontend'
)

function Get-LegacyContainers {
    docker ps -a --format '{{.Names}}' 2>$null | Where-Object {
        $_ -match '^(ipa_|intelligent_ai_)'
    }
}

function Get-LegacyImages {
    docker images --format '{{.Repository}}:{{.Tag}}' 2>$null | Where-Object {
        $repo = ($_ -split ':')[0]
        $legacyImageRepos -contains $repo
    }
}

Write-Host '=== Legacy Docker cleanup (ipa / intelligent_ai) ===' -ForegroundColor Cyan
Write-Host "Project root: $Root"
Write-Host ''

$containers = @(Get-LegacyContainers)
if ($containers.Count -gt 0) {
    Write-Host "Containers ($($containers.Count)):" -ForegroundColor Yellow
    $containers | ForEach-Object { Write-Host "  - $_" }
}

$images = @(Get-LegacyImages)
if ($images.Count -gt 0) {
    Write-Host "Images ($($images.Count)):" -ForegroundColor Yellow
    $images | ForEach-Object { Write-Host "  - $_" }
}

if (-not $Apply) {
    Write-Host ''
    Write-Host 'Preview only. Run: .\scripts\cleanup-legacy-docker.ps1 -Apply [-RemoveImages]' -ForegroundColor Green
    exit 0
}

foreach ($proj in $legacyProjects) {
    $listed = docker compose ls --format json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($listed | Where-Object { $_.Name -eq $proj }) {
        Write-Host "docker compose -p $proj down ..."
        docker compose -p $proj -f docker-compose.yml down --remove-orphans 2>$null
    }
}

foreach ($name in $containers) {
    Write-Host "docker rm -f $name"
    docker rm -f $name 2>$null | Out-Null
}

if ($RemoveImages -and $images.Count -gt 0) {
    foreach ($img in $images) {
        Write-Host "docker rmi $img"
        docker rmi -f $img 2>$null | Out-Null
    }
}

Write-Host ''
Write-Host 'Done. Rebuild iap stack:' -ForegroundColor Green
Write-Host '  docker compose --env-file dev.env build'
Write-Host '  docker compose --env-file dev.env up -d'
