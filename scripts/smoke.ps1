$ErrorActionPreference = "Stop"

$baseUrl = if ($env:JOBMATCH_BASE_URL) { $env:JOBMATCH_BASE_URL.TrimEnd('/') } else { "http://127.0.0.1:8000" }

function Get-Json($path) {
    return Invoke-RestMethod -Uri "$baseUrl$path" -Method Get
}

$health = Get-Json "/health"
$ready = Get-Json "/health/ready"
$capabilities = Get-Json "/api/v1/system/capabilities"
$serialized = @($health, $ready, $capabilities) | ConvertTo-Json -Depth 8 -Compress

foreach ($forbidden in @("api_key", "base_url", "resume_text", "jd_text")) {
    if ($serialized -match $forbidden) {
        throw "smoke response contains forbidden field: $forbidden"
    }
}

if ($health.status -ne "ok") { throw "health check failed" }
if ($ready.status -ne "ready" -or -not $ready.database_ready) { throw "readiness check failed" }
foreach ($field in @(
    "app_version",
    "storage_mode",
    "llm_configured",
    "tavily_configured",
    "embedding_enabled",
    "retrieval_default_strategy",
    "evidence_feedback_enabled"
)) {
    if ($null -eq $capabilities.$field) { throw "capabilities field missing: $field" }
}

Write-Output "smoke ok: $baseUrl"
