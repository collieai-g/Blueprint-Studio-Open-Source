#Requires -Version 7.0
param([string]$PrivateDirectory = (Join-Path $PSScriptRoot '../../../blueprint-private'))
$ErrorActionPreference = 'Stop'
$PrivateDirectory = [IO.Path]::GetFullPath($PrivateDirectory)
$envPath = Join-Path $PrivateDirectory '.env'
if (Test-Path $envPath) { throw "Existing private configuration preserved: $envPath" }
if ((Test-Path (Join-Path $PrivateDirectory 'secrets/signing.p12')) -or (Test-Path (Join-Path $PrivateDirectory 'secrets/s3.json'))) { throw 'Existing service secrets preserved. Choose a new private directory or restore its .env.' }
[IO.Directory]::CreateDirectory($PrivateDirectory) | Out-Null
$secretDirectory = Join-Path $PrivateDirectory 'secrets'
$backupDirectory = Join-Path $PrivateDirectory 'backups'
[IO.Directory]::CreateDirectory($secretDirectory) | Out-Null
[IO.Directory]::CreateDirectory($backupDirectory) | Out-Null
if ($IsWindows) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inheritance, 'None', 'Allow'))
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'), 'FullControl', $inheritance, 'None', 'Allow'))
    Set-Acl -Path $PrivateDirectory -AclObject $acl
} else {
    foreach ($directory in @($PrivateDirectory, $secretDirectory, $backupDirectory)) { [IO.File]::SetUnixFileMode($directory, [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite -bor [IO.UnixFileMode]::UserExecute) }
}
function New-Secret([int]$Length = 32) {
    return [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes($Length)).ToLowerInvariant()
}
$values = [ordered]@{
    COMPOSE_PROJECT_NAME='blueprint-workspace'; POSTGRES_PASSWORD=(New-Secret)
    BLUEPRINT_SETUP_TOKEN=(New-Secret); BLUEPRINT_MASTER_KEY=(New-Secret)
    BLUEPRINT_PUBLIC_URL='http://127.0.0.1:8787'; BLUEPRINT_PORT='8787'; BLUEPRINT_LISTEN_ADDRESS='127.0.0.1'
    BLUEPRINT_SECRETS_DIR=$secretDirectory.Replace('\','/')
    BLUEPRINT_S3_ACCESS_KEY=(New-Secret 12); BLUEPRINT_S3_SECRET_KEY=(New-Secret); BLUEPRINT_S3_BUCKET='blueprint'; BLUEPRINT_S3_REGION='us-east-1'
    DOCUMENSO_DB_PASSWORD=(New-Secret); DOCUMENSO_AUTH_SECRET=(New-Secret)
    DOCUMENSO_ENCRYPTION_KEY=(New-Secret); DOCUMENSO_SECONDARY_KEY=(New-Secret); DOCUMENSO_SIGNING_PASSWORD=(New-Secret)
    BLUEPRINT_DOCUMENSO_WEBHOOK_SECRET=(New-Secret); BLUEPRINT_DOCUMENSO_API_TOKEN=''
    BLUEPRINT_DOCUMENSO_PUBLIC_URL='http://127.0.0.1:3001'; KEYCLOAK_DB_PASSWORD=(New-Secret)
    KEYCLOAK_ADMIN_USER='blueprint-admin'; KEYCLOAK_ADMIN_PASSWORD=(New-Secret)
    BLUEPRINT_KEYCLOAK_PUBLIC_URL='http://127.0.0.1:8081'; COLLABORA_ADMIN_USER='blueprint-admin'
    BLUEPRINT_OIDC_ISSUER=''; BLUEPRINT_OIDC_REALM='blueprint'; BLUEPRINT_OIDC_CLIENT_ID='blueprint'; BLUEPRINT_OIDC_CLIENT_SECRET=''
    COLLABORA_ADMIN_PASSWORD=(New-Secret); BLUEPRINT_COLLABORA_PUBLIC_URL='http://127.0.0.1:9980'
    BLUEPRINT_COLLABORA_WOPI_ORIGIN='http://app:8787'; BLUEPRINT_GOOGLE_CLIENT_ID=''; BLUEPRINT_GOOGLE_CLIENT_SECRET=''
    BLUEPRINT_MICROSOFT_CLIENT_ID=''; BLUEPRINT_MICROSOFT_CLIENT_SECRET=''; SMTP_HOST=''; SMTP_PORT='587'
    SMTP_USERNAME=''; SMTP_PASSWORD=''; SMTP_FROM_NAME='Blueprint Studio'; SMTP_FROM_ADDRESS=''
}
$lines = @('# Private deployment configuration. Keep outside Git.')
foreach ($entry in $values.GetEnumerator()) { $lines += ('{0}="{1}"' -f $entry.Key, $entry.Value.Replace('"','\"')) }
[IO.File]::WriteAllLines($envPath, $lines, [Text.UTF8Encoding]::new($false))
$s3 = @{identities=@(@{name='blueprint'; credentials=@(@{accessKey=$values.BLUEPRINT_S3_ACCESS_KEY; secretKey=$values.BLUEPRINT_S3_SECRET_KEY}); actions=@('Admin','Read','List','Tagging','Write')})} | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $secretDirectory 's3.json'), $s3, [Text.UTF8Encoding]::new($false))
$rsa = [Security.Cryptography.RSA]::Create(3072)
try {
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Blueprint Studio document integrity, O=Self-hosted Blueprint Studio', $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $certificate = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddYears(10))
    try {
        $pfx = $certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pkcs12, $values.DOCUMENSO_SIGNING_PASSWORD)
        [IO.File]::WriteAllBytes((Join-Path $secretDirectory 'signing.p12'), $pfx)
    } finally { $certificate.Dispose() }
} finally { $rsa.Dispose() }
if (!$IsWindows) {
    [IO.File]::SetUnixFileMode($envPath, [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite)
    foreach ($name in @('s3.json','signing.p12')) { [IO.File]::SetUnixFileMode((Join-Path $secretDirectory $name), [IO.UnixFileMode]::UserRead -bor [IO.UnixFileMode]::UserWrite -bor [IO.UnixFileMode]::GroupRead -bor [IO.UnixFileMode]::OtherRead) }
}
Write-Host "Private configuration created in $PrivateDirectory"
Write-Host 'Open .env locally to copy BLUEPRINT_SETUP_TOKEN into first-run setup. No secrets were printed.'
