#Requires -Version 7.2
[CmdletBinding()]
param(
    [string]$PrivateDirectory = (Join-Path $PSScriptRoot '../../../blueprint-private'),
    [ValidateSet('basic','documents','enterprise')][string]$Profile = 'basic'
)
$ErrorActionPreference = 'Stop'
# Keep native exit-code handling consistent with PowerShell 7.2. This is a
# script-local preference; the calling shell's preferences are untouched.
$PSNativeCommandUseErrorActionPreference = $false
$profileExplicit = $PSBoundParameters.ContainsKey('Profile')
function Stop-Installation([string]$Message) {
    [Console]::Error.WriteLine("Installation stopped: $Message")
    exit 1
}
function Get-PhysicalDirectory([string]$Path) {
    # Resolve ancestor links too, so a junction cannot put secrets in this checkout.
    $absolute = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $current = [IO.Path]::GetPathRoot($absolute)
    foreach ($part in $absolute.Substring($current.Length).Split([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringSplitOptions]::RemoveEmptyEntries)) {
        $entry = [IO.DirectoryInfo]::new((Join-Path $current $part))
        $target = $entry.ResolveLinkTarget($true)
        $current = if ($target) { $target.FullName } else { $entry.FullName }
    }
    return $current
}
$repo = Get-PhysicalDirectory (Join-Path $PSScriptRoot '../..')
if (!(Test-Path -LiteralPath (Join-Path $repo 'compose.yaml')) -or !(Test-Path -LiteralPath (Join-Path $repo 'Dockerfile'))) {
    Stop-Installation 'Run this installer from a complete Blueprint source download.'
}
$dockerCommand = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue
if (!$dockerCommand) { Stop-Installation 'Install Docker Desktop (Windows/macOS) or Docker Engine with Compose and Buildx (Linux), then run this command again.' }
$dockerOs = (& $dockerCommand.Source info --format '{{.OSType}}' 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Stop-Installation 'Docker is not running or is unavailable to this account. Start Docker Desktop/Engine, then run this command again.' }
if ($dockerOs -ne 'linux') { Stop-Installation 'Docker must use Linux containers. Switch Docker Desktop to Linux containers, then retry.' }
$composeVersion = (& $dockerCommand.Source compose version --short 2>$null | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $composeVersion -notmatch '^v?([0-9]+)\.' -or [int]$Matches[1] -lt 2) {
    Stop-Installation 'Docker Compose v2 or newer is required. Update Docker Desktop or install the Compose plugin.'
}
& $dockerCommand.Source buildx version *> $null
if ($LASTEXITCODE -ne 0) { Stop-Installation 'Docker Buildx is required. Update Docker Desktop or install the Buildx plugin.' }
if (!$IsWindows -and !([IO.File].GetMethods() | Where-Object Name -eq SetUnixFileMode)) {
    Stop-Installation 'Use install.sh on this system, or update PowerShell so .NET supports private Unix file permissions.'
}
if ((Test-Path -LiteralPath $PrivateDirectory) -and !(Test-Path -LiteralPath $PrivateDirectory -PathType Container)) {
    Stop-Installation 'The private configuration path is an existing file. Choose a directory.'
}
[IO.Directory]::CreateDirectory($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PrivateDirectory)) | Out-Null
$PrivateDirectory = Get-PhysicalDirectory $PrivateDirectory
$comparison = if ($IsWindows) { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
$repoPrefix = $repo.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($PrivateDirectory.Equals($repo, $comparison) -or $PrivateDirectory.StartsWith($repoPrefix, $comparison)) {
    Stop-Installation 'Choose a private directory outside the Blueprint program folder.'
}
$envPath = Join-Path $PrivateDirectory '.env'
$marker = Join-Path $PrivateDirectory '.blueprint-install-profile'
if (Test-Path -LiteralPath $marker) {
    $savedProfile = [IO.File]::ReadAllText($marker).TrimEnd([char[]]"`r`n")
    if ($savedProfile -notin @('basic','documents','enterprise')) { Stop-Installation 'The saved installation profile is invalid. Inspect .blueprint-install-profile locally; no configuration was replaced.' }
    if ($profileExplicit -and $Profile -ne $savedProfile) { Stop-Installation "This installation uses $savedProfile. Changing profiles needs a separate reviewed upgrade; its configuration and data were preserved." }
    $Profile = $savedProfile
} elseif ((Test-Path -LiteralPath $envPath) -and !$profileExplicit) {
    Stop-Installation 'Existing configuration has no saved installer profile. Rerun with -Profile basic, documents or enterprise matching the existing installation. No configuration was replaced.'
}
if (Test-Path -LiteralPath $envPath) {
    Write-Host 'Reusing the existing private configuration and company volumes.'
} else {
    Write-Host 'Creating private configuration outside the program folder.'
    & (Join-Path $PSScriptRoot 'configure.ps1') -PrivateDirectory $PrivateDirectory
    # Only fresh configuration gets a path-specific namespace. Reruns never
    # rewrite .env or accidentally attach another company's Compose volumes.
    $identityPath = if ($IsWindows) { $PrivateDirectory.ToLowerInvariant() } else { $PrivateDirectory }
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($identityPath))).ToLowerInvariant().Substring(0,12)
    $lines = [IO.File]::ReadAllLines($envPath) | ForEach-Object { if ($_ -match '^COMPOSE_PROJECT_NAME=') { "COMPOSE_PROJECT_NAME=blueprint-$hash" } else { $_ } }
    [IO.File]::WriteAllLines($envPath, [string[]]$lines, [Text.UTF8Encoding]::new($false))
}
[IO.File]::WriteAllText($marker, "$Profile`n", [Text.UTF8Encoding]::new($false))
if (!$IsWindows) { [IO.File]::SetUnixFileMode($marker, [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite) }

function Invoke-Stack([string[]]$ComposeArguments, [switch]$Capture) {
    # A new process prevents the helper's exit and environment cleanup from
    # changing the caller's PowerShell session or Docker configuration.
    $command = @'
$ErrorActionPreference = 'Stop'
$composeArguments = [string[]](ConvertFrom-Json $env:BLUEPRINT_INSTALL_ARGUMENTS)
& $env:BLUEPRINT_INSTALL_STACK -PrivateDirectory $env:BLUEPRINT_INSTALL_PRIVATE -Profile $env:BLUEPRINT_INSTALL_PROFILE -ComposeArguments $composeArguments
exit $LASTEXITCODE
'@
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $PSHOME $(if ($IsWindows) { 'pwsh.exe' } else { 'pwsh' })
    $start.UseShellExecute = $false
    foreach ($argument in @('-NoProfile','-EncodedCommand',[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command)))) { $start.ArgumentList.Add($argument) }
    foreach ($name in @($start.Environment.Keys)) {
        if ($name -match '^(BLUEPRINT_|DOCUMENSO_|KEYCLOAK_|COLLABORA_|SMTP_|COMPOSE_)' -or $name -in @('POSTGRES_PASSWORD','RUST_LOG')) {
            $start.Environment.Remove($name) | Out-Null
        }
    }
    $start.Environment['BLUEPRINT_INSTALL_STACK'] = Join-Path $PSScriptRoot 'stack.ps1'
    $start.Environment['BLUEPRINT_INSTALL_PRIVATE'] = $PrivateDirectory
    $start.Environment['BLUEPRINT_INSTALL_PROFILE'] = $Profile
    $start.Environment['BLUEPRINT_INSTALL_ARGUMENTS'] = ConvertTo-Json -InputObject $ComposeArguments -Compress
    if ($Capture) { $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        $process.Start() | Out-Null
        if ($Capture) { $outputTask = $process.StandardOutput.ReadToEndAsync(); $errorTask = $process.StandardError.ReadToEndAsync() }
        $process.WaitForExit()
        $output = if ($Capture) { $outputTask.GetAwaiter().GetResult() } else { '' }
        if ($Capture) { $errorTask.GetAwaiter().GetResult() | Out-Null }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $output }
    } finally { $process.Dispose() }
}
Write-Host "Starting the $Profile installation. The first download and build can take several minutes."
$result = Invoke-Stack -ComposeArguments @('up','-d','--build')
if ($result.ExitCode -ne 0) { Stop-Installation 'Docker could not start the installation. Your private configuration and persistent volumes were kept; rerun the same command after resolving the Docker error.' }
$health = Invoke-Stack -ComposeArguments @('exec','-T','app','curl','--fail','--silent','--max-time','10','http://127.0.0.1:8787/health') -Capture
if ($health.ExitCode -ne 0) { Stop-Installation 'The app is not healthy yet. Your configuration and volumes were kept; check Docker Desktop and rerun this command.' }
try { $healthValue = ConvertFrom-Json $health.Output } catch { Stop-Installation 'The app returned an invalid health response.' }
if ($healthValue.status -ne 'ok') { Stop-Installation 'The app did not report a healthy database connection.' }
$running = Invoke-Stack -ComposeArguments @('ps','--status','running','--services') -Capture
if ($running.ExitCode -ne 0) { Stop-Installation 'Could not check the installation containers.' }
foreach ($service in @('app','postgres','worker')) {
    if ($service -notin ($running.Output -split '\r?\n')) { Stop-Installation "The $service service is not running. Your configuration and volumes were kept." }
}
$status = Invoke-Stack -ComposeArguments @('exec','-T','app','curl','--fail','--silent','--max-time','10','http://127.0.0.1:8787/api/v1/status') -Capture
if ($status.ExitCode -ne 0) { Stop-Installation 'The app started but its first-run status is unavailable.' }
try { $statusValue = ConvertFrom-Json $status.Output } catch { Stop-Installation 'The app returned an invalid first-run response.' }
if ($statusValue.initialized -isnot [bool]) { Stop-Installation 'The app returned an invalid first-run response.' }
$urlLine = [IO.File]::ReadLines($envPath) | Where-Object { $_ -match '^BLUEPRINT_PUBLIC_URL=' } | Select-Object -First 1
$publicUrl = if ($urlLine) { ($urlLine -replace '^[^=]*=', '').Trim('"',"'") } else { 'http://127.0.0.1:8787' }
$parsedUrl = $null
if (![Uri]::TryCreate($publicUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or $parsedUrl.Scheme -notin @('http','https') -or !$parsedUrl.Host -or $parsedUrl.UserInfo -or $parsedUrl.Query -or $parsedUrl.Fragment -or $publicUrl -match '[\r\n]') {
    Stop-Installation 'Inspect BLUEPRINT_PUBLIC_URL locally; use an HTTP(S) browser address without credentials, query or fragment.'
}
Write-Host "`nBlueprint is ready. Open: $publicUrl"
if ($statusValue.initialized -eq $true) {
    Write-Host 'Sign in to your existing company. Existing configuration and data were reused.'
} else {
    Write-Host "Complete company setup in your browser. Open $envPath locally and copy BLUEPRINT_SETUP_TOKEN into the setup page."
}
Write-Host 'No passwords, setup codes or API tokens have been printed.'
if ($Profile -ne 'basic') { Write-Host 'Optional services still need their accounts, credentials and connection checks in the admin settings.' }
