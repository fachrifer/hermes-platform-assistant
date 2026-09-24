# Build and copy the Phase 1b fleet to the Lab VM (run on the PC).
# Ships committed files only (git archive), so no env file, cert or rendered
# secret can leave the PC. Needs the SSH key installed on the VM once.
#
#   powershell -ExecutionPolicy Bypass -File deploy\office-assistant\scripts\ship-phase1b.ps1
param(
    [string]$Remote = "timai@10.216.4.80",
    [string]$Dir = "/home/timai/hermes-assistant",
    [string]$HermesImage = "nousresearch/hermes-agent:v2026.9.21",
    [switch]$SkipImages
)
$ErrorActionPreference = "Stop"
$key = "$env:USERPROFILE\.ssh\id_ed25519_hermes_lab"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$out = Join-Path $repo "deploy\office-assistant\images"
$paths = @("deploy/office-assistant", "office_gateway", "Dockerfile.office-gateway")

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

Push-Location $repo
try {
    $dirty = git -c core.fileMode=false status --porcelain -- $paths
    if ($dirty) { throw "uncommitted changes under shipped paths; commit first:`n$($dirty -join "`n")" }
    $crlf = git ls-files --eol -- $paths | Where-Object { $_ -match '^i/(crlf|mixed)' }
    if ($crlf) { throw "CRLF files in the index; fix line endings first:`n$($crlf -join "`n")" }
    $rev = (git rev-parse --short HEAD).Trim()

    New-Item -ItemType Directory -Force $out | Out-Null
    $tree = Join-Path $out "fleet-p1b-tree.tgz"
    $gateway = Join-Path $out "fleet-p1b-gateway.tgz"
    # A subtree archive does not see the root .gitattributes, so Git for Windows'
    # system core.autocrlf=true would turn every file into CRLF.
    $noConvert = @("-c", "core.autocrlf=false", "-c", "core.eol=lf")
    Invoke-Checked "git archive tree" { git @noConvert archive --format=tar.gz -o $tree "HEAD:deploy/office-assistant" }
    Invoke-Checked "git archive gateway" { git @noConvert archive --format=tar.gz -o $gateway HEAD office_gateway Dockerfile.office-gateway }
    $unpack = Join-Path $repo "deploy\office-assistant\scripts\unpack-phase1b.sh"
    if ([IO.File]::ReadAllText($unpack).Contains("`r")) { throw "unpack-phase1b.sh has CRLF line endings" }
    $files = @($tree, $gateway, $unpack)

    if (-not $SkipImages) {
        $images = Join-Path $out "fleet-p1b-images.tar"
        Invoke-Checked "docker build" { docker build -f "$repo\Dockerfile.office-gateway" -t office-gw:local $repo }
        Invoke-Checked "docker save" { docker save $HermesImage office-gw:local -o $images }
        $files += $images
    }

    Invoke-Checked "ssh mkdir" { ssh -i $key -o BatchMode=yes $Remote "mkdir -p '$Dir/images'" }
    Invoke-Checked "scp" { scp -i $key -o BatchMode=yes @files "${Remote}:${Dir}/images/" }
    foreach ($f in $files) {
        "{0,-28} {1,8:N1} MB" -f (Split-Path $f -Leaf), ((Get-Item $f).Length / 1MB)
    }
    "shipped commit $rev to ${Remote}:${Dir}/images/"
    "on the VM: docker compose down --remove-orphans; bash images/unpack-phase1b.sh; ./scripts/migrate-env-phase1b.sh"
}
finally {
    Pop-Location
}
