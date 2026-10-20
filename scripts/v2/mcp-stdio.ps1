#Requires -Version 7.2
param([string]$PrivateDirectory = (Join-Path $PSScriptRoot '../../../blueprint-private'))
$ErrorActionPreference = 'Stop'
$exitCode = 1
# Never write credentials or status messages to the MCP stdout stream.
try {
    $PrivateDirectory = [IO.Path]::GetFullPath($PrivateDirectory)
    if (!(Test-Path (Join-Path $PrivateDirectory '.env') -PathType Leaf)) { throw 'Blueprint is not configured. Run the installer first.' }
    $tokenFile = Join-Path $PrivateDirectory 'codex-token'
    if (!(Test-Path $tokenFile -PathType Leaf)) { throw 'Save a scoped Blueprint API token in the private codex-token file, then retry.' }
    $token = [IO.File]::ReadAllText($tokenFile).TrimEnd("`r", "`n")
    if (!$token -or $token.Length -gt 16384 -or $token -match '\s|[\x00-\x1f\x7f]') { throw 'The private codex-token file must contain one nonempty token.' }
    $profile = 'basic'
    $profileFile = Join-Path $PrivateDirectory '.blueprint-install-profile'
    if (Test-Path $profileFile -PathType Leaf) { $profile = [IO.File]::ReadAllText($profileFile).Trim() }
    if ($profile -notin @('basic', 'documents', 'enterprise')) { throw 'Invalid saved installation profile.' }
    if (!(Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1)) { throw 'Docker is required for the Blueprint MCP connection.' }
    # Run the stack in a new process: inherited company settings must not select
    # another Compose project or send this token into another company's app.
    # Docker context/socket, PATH, proxies and the session CA remain inherited.
    $stackPath = (Join-Path $PSScriptRoot 'stack.ps1').Replace("'", "''")
    $escapedPrivate = $PrivateDirectory.Replace("'", "''")
    $command = "& '$stackPath' -PrivateDirectory '$escapedPrivate' -Profile '$profile' -ComposeArguments @('exec', '-T', '-e', 'BLUEPRINT_MCP_TOKEN', 'app', 'blueprint-backend', 'mcp-stdio', '--url', 'http://127.0.0.1:8787', '--token-env', 'BLUEPRINT_MCP_TOKEN'); exit `$LASTEXITCODE"
    $pwsh = (Get-Command pwsh -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $start = [Diagnostics.ProcessStartInfo]::new($pwsh)
    $start.UseShellExecute = $false
    foreach ($argument in @('-NoLogo', '-NoProfile', '-NonInteractive', '-Command', $command)) { $start.ArgumentList.Add($argument) }
    foreach ($name in @($start.Environment.Keys)) {
        if ($name -match '^(BLUEPRINT_|COMPOSE_|DOCUMENSO_|KEYCLOAK_|COLLABORA_|SMTP_)' -or $name -in @('POSTGRES_PASSWORD', 'RUST_LOG')) {
            $null = $start.Environment.Remove($name)
        }
    }
    $start.Environment['BLUEPRINT_MCP_TOKEN'] = $token
    $process = [Diagnostics.Process]::Start($start)
    try { $process.WaitForExit(); $exitCode = $process.ExitCode } finally { $process.Dispose() }
} catch {
    [Console]::Error.WriteLine('Blueprint MCP connection failed. Check Docker, the private configuration and token file.')
}
exit $exitCode
