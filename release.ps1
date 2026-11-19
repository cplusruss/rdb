param([string]$Message)
$ErrorActionPreference = 'Stop'
$Repo = 'cplusruss/rdb'
Set-Location $PSScriptRoot

function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "$exe $args failed ($LASTEXITCODE)" }
}

# stderr captured as text: under 'Stop', Windows PowerShell 5.1 turns a native stderr line into an exception
function Capture($exe) {
    $ErrorActionPreference = 'Continue'
    $out = & $exe @args 2>&1 | ForEach-Object { "$_" }
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
}

function Parse($v) {
    if ($v -notmatch '^v?(\d+)\.(\d+)\.(\d+)$') { throw "bad version '$v'" }
    [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"
}

# every module must byte-compile before anything is pushed
$py = if ($env:PY) { $env:PY } else { 'C:\Python\313\python.exe' }
Get-ChildItem src\*.py | ForEach-Object { Run $py -m py_compile $_.FullName }

$file = 'src\rdb_version.py'
$text = Get-Content $file -Raw
if ($text -notmatch 'VERSION = "(\d+\.\d+\.\d+)"') { throw "VERSION not found in $file" }
$version = Parse $Matches[1]

$r = Capture gh release view --repo $Repo --json tagName --jq .tagName
if ($r.Code -eq 0) {
    $latest = Parse $r.Text.Trim()
} elseif ($r.Text -match 'release not found') {
    $latest = [version]'0.0.0'
} else {
    throw "gh release view failed: $($r.Text)"
}

if ($version -le $latest) {
    $version = [version]"$($latest.Major).$($latest.Minor).$($latest.Build + 1)"
    [IO.File]::WriteAllText((Resolve-Path $file), "VERSION = `"$version`"`n")
    Write-Host "VERSION bumped to $version (latest release v$latest)"
}

Run git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    if (-not $Message) { $Message = "Release v$version" }
    Run git commit -m $Message
}
Run git push
$sha = (git rev-parse HEAD).Trim()
$branch = (git rev-parse --abbrev-ref HEAD).Trim()

function Runs { gh run list --repo $Repo --workflow release.yml --limit 20 --json databaseId,headSha | ConvertFrom-Json }
$before = @(Runs | ForEach-Object { $_.databaseId })
Run gh workflow run release.yml --repo $Repo --ref $branch
$id = $null
for ($i = 0; $i -lt 30 -and -not $id; $i++) {
    Start-Sleep -Seconds 2
    $id = (Runs | Where-Object { $_.headSha -eq $sha -and $before -notcontains $_.databaseId } | Select-Object -First 1).databaseId
}
if (-not $id) { throw 'release workflow run did not appear' }
Run gh run watch $id --repo $Repo --exit-status

$rel = gh release view "v$version" --repo $Repo --json url,assets | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "release v$version not found after the run" }
Write-Host $rel.url
$rel.assets | ForEach-Object { Write-Host ("{0}  {1:N1} MB" -f $_.name, ($_.size / 1MB)) }
