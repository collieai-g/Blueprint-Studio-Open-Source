#Requires -Version 7.0
param(
    [string]$PrivateDirectory = (Join-Path $PSScriptRoot '../../../blueprint-private'),
    [ValidateSet('basic','documents','enterprise')][string]$Profile = 'basic',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$ComposeArguments
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$PrivateDirectory = [IO.Path]::GetFullPath($PrivateDirectory)
if (!(Test-Path (Join-Path $PrivateDirectory '.env'))) { throw 'Run configure.ps1 first.' }
$arguments = @('compose','--project-directory',$repo,'--env-file',(Join-Path $PrivateDirectory '.env'),'-f',(Join-Path $repo 'compose.yaml'))
if ($Profile -in @('documents','enterprise')) { $arguments += @('-f',(Join-Path $repo 'deploy/compose.documents.yaml')) }
if ($Profile -eq 'enterprise') { $arguments += @('-f',(Join-Path $repo 'deploy/compose.enterprise.yaml'),'--profile','enterprise') }
elseif ($Profile -eq 'documents') { $arguments += @('--profile','documents') }
if ($env:CODEX_PROXY_CERT) { $arguments += @('-f',(Join-Path $repo 'deploy/compose.proxy-ca.yaml')) }
if (!$ComposeArguments) { $ComposeArguments = @('up','-d','--build') }
& docker @arguments @ComposeArguments
exit $LASTEXITCODE
