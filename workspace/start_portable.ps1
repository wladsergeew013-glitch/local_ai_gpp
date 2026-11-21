param(
    [string]$McpLauncher, [string]$SkillManifest, [string]$BundleRoot,
    [string]$InstallRoot, [string]$WorkspaceExe, [string]$ProfileDirectory
)
$ErrorActionPreference = 'Stop'
$repo = if ($InstallRoot) { (Get-Item -LiteralPath $InstallRoot).FullName } else { Split-Path -Parent $PSScriptRoot }
$runtime = Join-Path $repo 'dist\runtime\LocalAIGPP.exe'
$workspace = if ($WorkspaceExe) { (Get-Item -LiteralPath $WorkspaceExe).FullName } else { Join-Path $repo 'dist\worker\LocalAIWorkspace.exe' }
$profile = if ($ProfileDirectory) { [System.IO.Path]::GetFullPath($ProfileDirectory) } else { Join-Path $repo 'workspace-profile' }
$workspaceArgs = if ($ProfileDirectory) { '--data-dir "{0}"' -f $profile } else { '--portable' }

function Get-LocalOwner([int]$Port, [string]$ExpectedExe) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') } |
        Select-Object -First 1
    if ($null -eq $listener) { return }
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    if ($process.Path -ne $ExpectedExe) {
        throw "Port $Port is in use by a different installation. Expected $ExpectedExe."
    }
    return $process
}

function Wait-ForOwner([int]$Port, [string]$ExpectedExe) {
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        $process = Get-LocalOwner $Port $ExpectedExe
        if ($process) { return $process }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Startup timed out for $ExpectedExe on port $Port."
}

foreach ($file in @($runtime, $workspace)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Package file missing: $file" }
}
if ($BundleRoot -and -not $SkillManifest) { throw '-BundleRoot requires -SkillManifest.' }
if ($SkillManifest) {
    $manifest = (Get-Item -LiteralPath $SkillManifest -ErrorAction Stop).FullName
    $importArgs = '{0} --import-skills "{1}"' -f $workspaceArgs, $manifest
    if ($BundleRoot) {
        $bundle = (Get-Item -LiteralPath $BundleRoot -ErrorAction Stop).FullName
        if ($bundle.EndsWith('\')) { $bundle += '.' }
        $importArgs += ' --bundle-root "{0}"' -f $bundle
    }
}
if ($McpLauncher) {
    $script = (Resolve-Path -LiteralPath $McpLauncher -ErrorAction Stop).Path
    & $script
    if (-not $?) { throw "Could not start the local MCP service." }
}
if ($SkillManifest) {
    $import = Start-Process -FilePath $workspace -ArgumentList $importArgs -WorkingDirectory $repo -WindowStyle Hidden -Wait -PassThru
    if ($import.ExitCode -ne 0) {
        throw "Skill import failed (exit $($import.ExitCode)). Check $profile\logs\desktop.log."
    }
}
if (-not (Get-LocalOwner 8765 $runtime)) {
    & (Join-Path (Split-Path -Parent $runtime) 'RUN_LocalAIGPP.bat')
    $null = Wait-ForOwner 8765 $runtime
}
$process = Get-LocalOwner 8776 $workspace
if ($process) {
    if ($process.MainWindowHandle -ne 0) {
        $null = (New-Object -ComObject WScript.Shell).AppActivate($process.Id)
    }
} else {
    Start-Process -FilePath $workspace -ArgumentList $workspaceArgs -WorkingDirectory (Split-Path -Parent $workspace)
    $null = Wait-ForOwner 8776 $workspace
}
