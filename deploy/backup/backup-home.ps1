<#
Nightly encrypted backup on the Windows home PC, to an encrypted external drive.
Excludes resume data; deletes backups older than 30 days.

  powershell -ExecutionPolicy Bypass -File deploy\backup\backup-home.ps1 -Destination E:\skills-gap-backups

Needs pg_dump.exe and age.exe on PATH, DATABASE_URL and BACKUP_AGE_RECIPIENT in the environment.
#>
param([Parameter(Mandatory = $true)][string]$Destination)
$ErrorActionPreference = "Stop"
if (-not $env:DATABASE_URL) { throw "DATABASE_URL is not set" }
if (-not $env:BACKUP_AGE_RECIPIENT) { throw "BACKUP_AGE_RECIPIENT is not set" }

$pgUrl = $env:DATABASE_URL -replace '^postgresql\+psycopg:', 'postgresql:'
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$temp = Join-Path $env:TEMP "skills_gap_$stamp.dump"
New-Item -ItemType Directory -Force $Destination | Out-Null
try {
    & pg_dump $pgUrl --format=custom --no-owner --exclude-table-data=resumes --exclude-table-data=analyses --file=$temp
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
    & age --encrypt --recipient $env:BACKUP_AGE_RECIPIENT --output (Join-Path $Destination "skills_gap_$stamp.dump.age") $temp
    if ($LASTEXITCODE -ne 0) { throw "age failed" }
} finally {
    Remove-Item $temp -ErrorAction SilentlyContinue
}
Get-ChildItem $Destination -Filter "skills_gap_*.dump.age" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item
Write-Host "Backup written to $Destination"
