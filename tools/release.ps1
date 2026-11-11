# One-command release: version bump, build, local install, zip, Scoop manifest, commit, push, GitHub release.
#   powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Version 1.9.0 [-InstallTarget D:\Tools\FlipperWorkbench] [-DryRun]
# Needs release notes in .cache\release-notes-<version>.md and the CHANGELOG entry written first.
# Pushes as the GitHub account c3rb3ruszx and switches gh back to the previous account afterwards.
# Commits carry no co-author trailer (c3rb3ruszx is the only contributor).
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$InstallTarget = '',
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.9.0" }
$notes = Join-Path $root ".cache\release-notes-$Version.md"
if (-not (Test-Path $notes)) { throw "Write the release notes first: $notes" }
if (-not (Select-String -Path CHANGELOG.md -Pattern "^## $([regex]::Escape($Version))$" -Quiet)) { throw "CHANGELOG.md has no '## $Version' entry" }
if ((git tag --list "v$Version")) { throw "Tag v$Version already exists" }

function Step($t) { Write-Host "== $t" -ForegroundColor Yellow }
$parts = $Version.Split('.')

Step "Version $Version"
$utf8 = New-Object System.Text.UTF8Encoding($false)   # no BOM: PyInstaller's version file must stay plain
function Edit-File($path, $pattern, $replacement) {
    $full = Join-Path $root $path
    $text = [IO.File]::ReadAllText($full)
    $new = [regex]::Replace($text, $pattern, $replacement)
    if ($new -eq $text -and $text -notmatch [regex]::Escape($Version)) { throw "Version pattern not found in $path" }
    [IO.File]::WriteAllText($full, $new, $utf8)
}
Edit-File 'flipper_workbench.py' 'APP_VERSION = "[\d.]+"' "APP_VERSION = `"$Version`""
Edit-File 'tools\install.ps1' "\`$version = '[\d.]+'" "`$version = '$Version'"
Edit-File 'tools\version_info.txt' '\(\d+, \d+, \d+, 0\)' "($($parts[0]), $($parts[1]), $($parts[2]), 0)"
Edit-File 'tools\version_info.txt' "'\d+\.\d+\.\d+'" "'$Version'"

Step "Build"
& powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
if ($InstallTarget) {
    Step "Install to $InstallTarget"
    & powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Target $InstallTarget
}

Step "Zip"
$zip = Join-Path $root '.cache\FlipperWorkbench-win64.zip'
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\FlipperWorkbench -DestinationPath $zip
& .venv\Scripts\python.exe tools\make_manifests.py $Version $zip

Step "Privacy check"
# The words to look for live in a local, untracked file (one regex per line), so they never end up in the repo.
$patternsFile = Join-Path $root '.cache\privacy-patterns.txt'
if (-not (Test-Path $patternsFile)) { throw "Missing $patternsFile (one regex per line: names, emails, paths that must not be published)" }
$patterns = (Get-Content $patternsFile | Where-Object { $_.Trim() -and -not $_.StartsWith('#') }) -join '|'
# --untracked: new files are about to be committed by `git add -A` too (gitignored files stay excluded)
$hits = git grep --untracked -n -i -E $patterns -- . ':!ui/vendor'
if ($hits) { $hits; throw "Personal data found in tracked files" }

if ($DryRun) { Write-Host "Dry run: built and zipped, nothing committed or published." -ForegroundColor Green; exit 0 }

Step "Commit + push + release"
$active = ((gh api user -q .login) 2>$null)
git add -A
git commit -q -m "v$Version"
gh auth switch -u c3rb3ruszx | Out-Null
try {
    git push -q origin main
    gh release create "v$Version" $zip -R c3rb3ruszx/flipper-workbench --title "v$Version" --notes-file $notes --target main
} finally {
    if ($active -and $active -ne 'c3rb3ruszx') { gh auth switch -u $active | Out-Null }
}
Write-Host "Released v$Version" -ForegroundColor Green
