param(
    [string]$Version = "0.3.1",
    [switch]$SkipLite,
    [switch]$SkipFull,
    [switch]$SkipFullCuda,
    [switch]$SkipCudaRuntimeZip
)

$ErrorActionPreference = "Stop"

$Python = ".\.venv-win\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Missing $Python. Create .venv-win and install requirements first."
}
if (-not (Test-Path ".\.venv-win\Scripts\pyinstaller.exe")) {
    & $Python -m pip install pyinstaller==6.11.1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller."
    }
}

New-Item -ItemType Directory -Force release | Out-Null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [string[]]$Arguments
    )

    $argumentLine = ($Arguments | ForEach-Object {
        if ($_ -match '[\s";]') {
            '"' + ($_.Replace('"', '\"')) + '"'
        } else {
            $_
        }
    }) -join " "

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $argumentLine `
        -NoNewWindow `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Command failed with exit code $($process.ExitCode): $FilePath $($Arguments -join ' ')"
    }
}

function Compress-DirectoryWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -Force $DestinationPath -ErrorAction SilentlyContinue
            Compress-Archive -Path $SourceDir -DestinationPath $DestinationPath -CompressionLevel Optimal
            return
        } catch {
            if ($attempt -eq 5) {
                throw
            }
            Write-Warning "Compress-Archive failed on attempt $attempt. Retrying..."
            Start-Sleep -Seconds ([Math]::Min(10, $attempt * 2))
        }
    }
}

function Build-Portable {
    param(
        [string]$Edition,
        [string]$IncludeModel,
        [bool]$IncludeCudaRuntimeForPackage = $false
    )

    if ($IncludeModel -eq "1") {
        $modelRoot = ".models\models--Systran--faster-whisper-small"
        $modelRef = Join-Path $modelRoot "refs\main"
        $hasModelCache = $false
        if (Test-Path $modelRef) {
            $snapshot = (Get-Content $modelRef -Raw).Trim()
            if ($snapshot) {
                $snapshotRoot = Join-Path $modelRoot ("snapshots\" + $snapshot)
                $hasModelCache = (
                    (Test-Path (Join-Path $snapshotRoot "config.json")) -and
                    (Test-Path (Join-Path $snapshotRoot "model.bin")) -and
                    (Test-Path (Join-Path $snapshotRoot "tokenizer.json")) -and
                    (Test-Path (Join-Path $snapshotRoot "vocabulary.txt"))
                )
            }
        }
        if (-not $hasModelCache) {
            Invoke-Checked $Python @(
                "-c",
                "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='.models')"
            )
        } else {
            Write-Host "Using existing faster-whisper-small cache."
        }
    }

    $env:INCLUDE_MODEL = $IncludeModel
    $env:INCLUDE_CUDA_RUNTIME = if ($IncludeCudaRuntimeForPackage) { "1" } else { "0" }

    Remove-Item -Recurse -Force "dist\VoxGo" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "build\VoxGo" -ErrorAction SilentlyContinue
    Invoke-Checked $Python @("-m", "PyInstaller", "--clean", "--noconfirm", "VoxGo.spec")

    $packageName = "VoxGo-v$Version-$Edition"
    $packageDir = "release\$packageName"
    $zipPath = "release\$packageName.zip"

    Remove-Item -Recurse -Force $packageDir -ErrorAction SilentlyContinue
    Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
    Copy-Item -Recurse "dist\VoxGo" $packageDir
    Compress-DirectoryWithRetry -SourceDir $packageDir -DestinationPath $zipPath

    $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "$packageName.zip"
    Write-Host "  size: $size MB"
    Write-Host "  sha256: $hash"
}

function Assert-CudaRuntimeReady {
    $runtimeDir = "runtime\cuda"
    $requiredDlls = @(
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudart64_12.dll",
        "cudnn64_9.dll"
    )
    if (-not (Test-Path $runtimeDir)) {
        throw "Missing $runtimeDir. Run scripts\collect_cuda_runtime.ps1 before building Full-CUDA."
    }
    $missing = @()
    foreach ($name in $requiredDlls) {
        if (-not (Test-Path (Join-Path $runtimeDir $name))) {
            $missing += $name
        }
    }
    if ($missing.Count -gt 0) {
        throw "Missing CUDA runtime DLLs: $($missing -join ', ')"
    }
}

function Build-CudaRuntimeZip {
    if ($SkipCudaRuntimeZip) {
        return
    }

    Assert-CudaRuntimeReady
    $packageName = "VoxGo-v$Version-cuda-runtime"
    $packageDir = "release\$packageName"
    $zipPath = "release\$packageName.zip"

    Remove-Item -Recurse -Force $packageDir -ErrorAction SilentlyContinue
    Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $packageDir | Out-Null
    Get-ChildItem -Path "runtime\cuda" -Filter "*.dll" -File |
        Copy-Item -Destination $packageDir -Force
    Compress-DirectoryWithRetry -SourceDir $packageDir -DestinationPath $zipPath

    $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "$packageName.zip"
    Write-Host "  size: $size MB"
    Write-Host "  sha256: $hash"
}

if (-not $SkipLite) {
    Build-Portable -Edition "lite" -IncludeModel "0" -IncludeCudaRuntimeForPackage $false
}

if (-not $SkipFull) {
    Build-Portable -Edition "full" -IncludeModel "1" -IncludeCudaRuntimeForPackage $false
}

if (-not $SkipFullCuda) {
    Assert-CudaRuntimeReady
    Build-Portable -Edition "full-cuda" -IncludeModel "1" -IncludeCudaRuntimeForPackage $true
}

Build-CudaRuntimeZip

Remove-Item Env:\INCLUDE_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:\INCLUDE_CUDA_RUNTIME -ErrorAction SilentlyContinue
