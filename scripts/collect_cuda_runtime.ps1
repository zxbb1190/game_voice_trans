param(
    [string]$Python = ".\.venv-win\Scripts\python.exe",
    [string]$Destination = "runtime\cuda",
    [switch]$InstallNvidiaPipPackages
)

$ErrorActionPreference = "Stop"

$RequiredDlls = @(
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudart64_12.dll",
    "cudnn64_9.dll"
)

$OptionalDlls = @(
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_adv64_9.dll",
    "nvrtc64_120_0.dll",
    "nvrtc-builtins64_120.dll"
)

function Add-SearchRoot {
    param(
        [System.Collections.Generic.List[string]]$Roots,
        [string]$Path
    )
    if ($Path -and (Test-Path $Path)) {
        $resolved = (Resolve-Path $Path).Path
        if (-not $Roots.Contains($resolved)) {
            $Roots.Add($resolved)
        }
    }
}

if (-not (Test-Path $Python)) {
    throw "Missing Python: $Python"
}

if ($InstallNvidiaPipPackages) {
    & $Python -m pip install --upgrade nvidia-cublas-cu12 nvidia-cuda-runtime-cu12
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install NVIDIA CUDA runtime pip packages."
    }
}

$searchRoots = [System.Collections.Generic.List[string]]::new()
Add-SearchRoot $searchRoots "runtime\cuda-source"
Add-SearchRoot $searchRoots ".venv-win\Lib\site-packages\nvidia"
Add-SearchRoot $searchRoots ".venv\Lib\site-packages\nvidia"
Add-SearchRoot $searchRoots ".venv-win\Lib\site-packages\ctranslate2"
Add-SearchRoot $searchRoots ".venv\Lib\site-packages\ctranslate2"
Add-SearchRoot $searchRoots "$env:CUDA_PATH\bin"
Add-SearchRoot $searchRoots "${env:ProgramFiles}\NVIDIA GPU Computing Toolkit\CUDA"
Add-SearchRoot $searchRoots "${env:ProgramFiles}\NVIDIA\CUDNN"

New-Item -ItemType Directory -Force $Destination | Out-Null

$copied = @{}
$wanted = $RequiredDlls + $OptionalDlls
foreach ($root in $searchRoots) {
    foreach ($name in $wanted) {
        if ($copied.ContainsKey($name)) {
            continue
        }
        $match = Get-ChildItem -Path $root -Filter $name -Recurse -File -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) {
            Copy-Item -LiteralPath $match.FullName -Destination (Join-Path $Destination $name) -Force
            $copied[$name] = $match.FullName
        }
    }
}

$missingRequired = @()
foreach ($name in $RequiredDlls) {
    if (-not $copied.ContainsKey($name)) {
        $missingRequired += $name
    }
}

Write-Host "CUDA runtime collection:"
foreach ($name in ($copied.Keys | Sort-Object)) {
    Write-Host "  copied $name <- $($copied[$name])"
}

if ($missingRequired.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing required DLLs: $($missingRequired -join ', ')"
    Write-Host "Run this script with -InstallNvidiaPipPackages, install CUDA 12 locally, or place DLLs under runtime\cuda-source."
    exit 1
}

Write-Host ""
Write-Host "Done. Runtime DLLs are in $Destination"
