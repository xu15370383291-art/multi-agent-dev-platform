param(
    [ValidateSet('initialize','preflight','build','start','stop','status','usage')]
    [string]$Action = 'status',
    [switch]$HoldAutoResume
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'backend')
try {
    $platformArgs = @('-m', 'scripts.platform', $Action)
    if ($HoldAutoResume) { $platformArgs += '--hold-auto-resume' }
    & uv run --locked python @platformArgs
    if ($LASTEXITCODE -ne 0) { throw "Platform command failed; see the reported reason." }
} finally { Pop-Location }
