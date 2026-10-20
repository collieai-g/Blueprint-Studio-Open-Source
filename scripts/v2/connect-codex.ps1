#Requires -Version 7.2
param(
    [string]$PrivateDirectory = (Join-Path $PSScriptRoot '../../../blueprint-private'),
    [ValidatePattern('^[a-zA-Z0-9_-]{1,64}$')][string]$Name = 'blueprint'
)
$ErrorActionPreference = 'Stop'
if (!(Get-Command codex -ErrorAction SilentlyContinue)) { throw 'Install Codex CLI and sign in with your own account first.' }
$PrivateDirectory = [IO.Path]::GetFullPath($PrivateDirectory)
if (!(Test-Path (Join-Path $PrivateDirectory '.env') -PathType Leaf)) { throw 'Blueprint is not configured. Run the installer first.' }
$tokenFile = Join-Path $PrivateDirectory 'codex-token'
if (!(Test-Path $tokenFile -PathType Leaf) -or (Get-Item $tokenFile).Length -eq 0) { throw 'Create a scoped API token in Blueprint and save it in the private codex-token file. Do not paste it into chat.' }
$wrapper = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'mcp-stdio.ps1'))
$pwsh = (Get-Command pwsh).Source
$arguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $wrapper, '-PrivateDirectory', $PrivateDirectory)
$null = & codex mcp list --json 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Codex MCP configuration could not be read. Existing configuration was preserved.' }
$existingText = (& codex mcp get $Name --json 2>$null | Out-String)
if ($LASTEXITCODE -eq 0) {
    $existing = $existingText | ConvertFrom-Json
    $transport = $existing.transport
    if (!$existing.enabled -or $transport.type -cne 'stdio' -or $transport.command -cne $pwsh -or ($transport.args | ConvertTo-Json -Compress) -cne ($arguments | ConvertTo-Json -Compress)) {
        throw 'A different or disabled MCP configuration already uses this name. Existing configuration was preserved; use -Name with a new name.'
    }
    Write-Output 'The matching Blueprint MCP configuration already exists; no changes made.'
} else {
    & codex mcp add $Name -- $pwsh @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Codex MCP registration failed.' }
}
Write-Output 'Refresh MCP in Codex or open a new Codex conversation, then verify the Blueprint tools. Registration alone does not verify the running connection.'
