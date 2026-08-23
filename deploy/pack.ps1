#requires -Version 4.0
<#
.SYNOPSIS
  Pack Auto_ext (committed HEAD) into a git-free tarball for the red zone.

.DESCRIPTION
  Runs on the yellow zone (Windows). Uses `git archive`, so the package is built
  from committed blobs in the object store, NOT from the Windows working tree:
    * paths are always POSIX "/", never "\"
    * text is LF (the index stores LF; an autocrlf checkout cannot corrupt the
      package -- this is what keeps deploy.sh runnable by the red zone's bash)
    * no .git/, no .gitignore/.gitattributes, no pack.ps1, no docs/archive/ and
      no design canvas (all export-ignored in .gitattributes)
    * VERSION is stamped with the real commit hash + date via export-subst

  What ships is a BLACKLIST, not a whitelist: everything in the repo crosses the
  gap unless .gitattributes export-ignores it, so new modules, new tests and new
  recipes are packaged automatically and this script never needs touching. The
  packer it replaced kept a hand-written include list and had silently stopped
  shipping recipes/ -- a directory added months after that list was written.

  Emits two files under <OutDir>:
    <Name>_<shorthash>.tar.gz              the whole package (code + tests + docs)
    <Name>_<shorthash>.tar.gz.sha256
  Upload both; that is the entire code delivery.

  With -WithWheels, also emits the offline dependency bundle:
    <Name>_wheels_<count>.tar.gz  (+ .sha256)
  The wheels are gitignored, so `git archive` structurally cannot put them in
  the code package -- which is right, because they change perhaps twice a year
  while the code changes daily. They are their own upload, and deploy.sh
  refuses to treat one as a code package.

  Requires only git + PowerShell (Get-FileHash). No Python, no external tar
  (except for -WithWheels), no downloads.

.PARAMETER Ref
  Git ref to package (default HEAD).

.PARAMETER OutDir
  Output directory (default: <script dir>\dist).

.PARAMETER Name
  Package root directory name, i.e. what you get after `tar -xzf`, and the name
  of the red-zone install dir. Default 'Auto_ext_pro', matching the deployment
  path already in use. deploy.sh does not depend on this name.

.PARAMETER WithWheels
  Also pack wheels/ as a separate bundle (see above).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\pack.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\pack.ps1 -WithWheels
#>
param(
    [string]$Ref    = 'HEAD',
    [string]$OutDir = (Join-Path $PSScriptRoot 'dist'),
    [string]$Name   = 'Auto_ext_pro',
    [switch]$WithWheels
)
$ErrorActionPreference = 'Stop'

# Repo root = parent of this script's dir (script lives at <repo>\deploy\).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Same sentinel deploy.sh checks on the far side. Nested on purpose: a bare
# cli.py exists in half the repos on that box, auto_ext/core/runner.py does not.
$Sentinel = 'auto_ext/core/runner.py'
$Prefix   = $Name

# --- sanity ------------------------------------------------------------------
& git rev-parse --is-inside-work-tree 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw "Not a git work tree: $RepoRoot" }
if (-not (Test-Path (Join-Path $RepoRoot $Sentinel))) {
    throw "$Sentinel not found in $RepoRoot - run this from the Auto_ext repo."
}

$dirty = & git status --porcelain
if ($dirty) {
    Write-Warning "Working tree has uncommitted changes; they will NOT be packaged (git archive ships committed $Ref only). Commit them first."
}

$short = (& git rev-parse --short $Ref).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($short)) { throw "cannot resolve ref: $Ref" }

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# --- locate cmd.exe ----------------------------------------------------------
# The CR preflight below redirects a git stream to a file THROUGH cmd.exe,
# because PowerShell's own capture re-encodes the stream (LF -> CRLF) and would
# make the check pass on a package that is actually CRLF -- i.e. the check would
# lie in exactly the direction that bricks a deploy. Resolve cmd.exe from
# %ComSpec% / the system directory rather than from PATH: a hand-edited PATH
# that has lost C:\Windows\System32 still packs fine.
$ComSpec = $env:ComSpec
if ([string]::IsNullOrEmpty($ComSpec) -or -not (Test-Path -LiteralPath $ComSpec)) {
    $ComSpec = Join-Path ([Environment]::GetFolderPath('System')) 'cmd.exe'
}
if (-not (Test-Path -LiteralPath $ComSpec)) {
    throw "cmd.exe not found (ComSpec='$($env:ComSpec)'). It is required for byte-exact stream redirection - repair your PATH/environment."
}

# --- preflight: everything Linux executes MUST arrive as LF ------------------
# This is the single failure mode that bricks a red-zone deploy (bash reports
# $'\r': command not found). Catch it here, not over there. Two independent
# guards, because either one alone can be true while the package still comes
# out CRLF:
#   1. the blob in the index is LF
#   2. the `eol` attribute is lf, so `git archive` does not re-expand it via
#      core.eol=native (the Windows default). Without an explicit eol, a
#      `text`-marked file IS re-expanded -- verified, not theoretical.
# tests/mocks/* are in the list because they are shell scripts the shipped test
# suite EXECUTES: a CRLF mock makes `doctor.sh --test` fail on a perfectly good
# install, which reads as "the package did not land".
$shScripts = @(
    'deploy.sh',
    'deploy/doctor.sh',
    'run.sh',
    'scripts/install_offline.sh',
    'tests/mocks/_common.sh',
    'tests/mocks/calibre',
    'tests/mocks/jivaro',
    'tests/mocks/qrc',
    'tests/mocks/si',
    'tests/mocks/strmout'
)
foreach ($sh in $shScripts) {
    $eolInfo = & git ls-files --eol -- $sh
    if ([string]::IsNullOrEmpty($eolInfo)) { throw "$sh is not tracked by git - git add it first." }
    if ($eolInfo -notmatch 'i/lf') {
        throw "$sh has CRLF in the git index. The red zone's bash cannot run it. Fix with: git add --renormalize $sh"
    }
}

# Guard 2 inspects the ACTUAL ARCHIVE OUTPUT, not the working tree. `git
# check-attr` would read the working-tree .gitattributes, but `git archive`
# reads the one committed in $Ref -- so an uncommitted .gitattributes fix looks
# green while the package still ships CRLF. Archive the scripts on their own and
# scan the raw bytes: a tar holds ASCII headers plus file content, and neither
# may contain CR here, so a single byte scan is exact.
$probeTar = Join-Path $OutDir ("_pack_probe_{0}.tar" -f $PID)   # stays in OutDir, not the system temp
& $ComSpec /c "git archive --format=tar $Ref -- $($shScripts -join ' ') > ""$probeTar"" 2>nul"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $probeTar)) {
    if (Test-Path $probeTar) { Remove-Item -LiteralPath $probeTar -Force }
    throw "git archive cannot find all of: $($shScripts -join ', ') in $Ref - commit them (and .gitattributes) first."
}
$probeBytes = [System.IO.File]::ReadAllBytes($probeTar)
Remove-Item -LiteralPath $probeTar -Force
if ($probeBytes -contains [byte]13) {
    throw "git archive emits CRLF for the shell scripts, so the red zone's bash would fail on a stray carriage return. Ensure .gitattributes (with '* text=auto eol=lf') is COMMITTED in $Ref, not just saved."
}

# --- sha256 sidecar helper: "<hash>  <name>\n", LF + no BOM (GNU sha256sum) --
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-Sha256Sidecar([string]$FilePath) {
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $FilePath).Hash.ToLower()
    $n = Split-Path -Leaf $FilePath
    [System.IO.File]::WriteAllText("$FilePath.sha256", "$h  $n`n", $utf8NoBom)
    return $h
}

# --- pack the code -----------------------------------------------------------
$tarName = "${Prefix}_$short.tar.gz"
$tarPath = Join-Path $OutDir $tarName

Write-Host ">> packaging $Ref ($short) -> $tarPath"
& git archive --format=tar.gz --prefix="$Prefix/" -o $tarPath $Ref
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
$hash = Write-Sha256Sidecar $tarPath

# --- optionally pack the wheels ---------------------------------------------
# Not part of the code package and structurally unable to be: wheels/ is
# gitignored, so git archive cannot see it. Packed here only so the operator can
# move it across the gap with the same integrity guarantee as the code.
$wheelTarName = $null
$wheelHash    = $null
if ($WithWheels) {
    $wheelDir = Join-Path $RepoRoot 'wheels'
    $wheels = @(Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl' -ErrorAction SilentlyContinue)
    if ($wheels.Count -eq 0) {
        throw "-WithWheels given but no *.whl under $wheelDir. Run scripts\download_wheels.py first."
    }
    $tarExe = Join-Path $env:WINDIR 'System32\tar.exe'
    if (-not (Test-Path $tarExe)) {
        $tarCmd = Get-Command tar.exe -ErrorAction SilentlyContinue
        if ($null -eq $tarCmd) { throw "tar.exe not found; it is needed only for -WithWheels." }
        $tarExe = $tarCmd.Source
    }
    $wheelTarName = "${Prefix}_wheels_$($wheels.Count).tar.gz"
    $wheelTarPath = Join-Path $OutDir $wheelTarName
    if (Test-Path $wheelTarPath) { Remove-Item -LiteralPath $wheelTarPath -Force }
    Write-Host ">> packaging $($wheels.Count) wheels -> $wheelTarPath"
    # `wheels` as the single member: extracting with -C <install> lands them at
    # <install>/wheels/, which is where scripts/install_offline.sh looks.
    & $tarExe -czf $wheelTarPath -C $RepoRoot 'wheels'
    if ($LASTEXITCODE -ne 0) { throw "tar failed packing wheels (exit $LASTEXITCODE)" }
    $wheelHash = Write-Sha256Sidecar $wheelTarPath
}

# --- report ------------------------------------------------------------------
$size = '{0:N1} KB' -f ((Get-Item $tarPath).Length / 1KB)
Write-Host ""
Write-Host "OK  package : $tarPath  ($size)"
Write-Host "    sha256  : $hash"
if ($wheelTarName) {
    $wsize = '{0:N1} MB' -f ((Get-Item (Join-Path $OutDir $wheelTarName)).Length / 1MB)
    Write-Host "    wheels  : $(Join-Path $OutDir $wheelTarName)  ($wsize)"
    Write-Host "    sha256  : $wheelHash"
}
Write-Host ""
Write-Host "commit info:"
& git --no-pager show -s --format='    %h  %cI  %s' $Ref
Write-Host ""
Write-Host "NEXT -- upload BOTH files into  .../$Prefix/ :"
Write-Host "       $tarName"
Write-Host "       $tarName.sha256"
Write-Host "     then on the red zone (login shell is often tcsh -- use bash):"
Write-Host "       cd .../$Prefix"
Write-Host "       bash deploy.sh                    # picks up the tarball sitting here"
Write-Host "       bash deploy/doctor.sh --test      # verify the box can run it"
Write-Host ""
Write-Host "     (first time only: tar -xzf $tarName  ->  ./$Prefix/ is the install)"
if ($wheelTarName) {
    Write-Host ""
    Write-Host "     wheels are a SEPARATE upload, needed only when the dependency set"
    Write-Host "     changed. Unpack them yourself -- deploy.sh deliberately refuses to"
    Write-Host "     treat a wheels bundle as a code package:"
    Write-Host "       tar -xzf $wheelTarName -C .../$Prefix"
    Write-Host "       bash scripts/install_offline.sh"
}
