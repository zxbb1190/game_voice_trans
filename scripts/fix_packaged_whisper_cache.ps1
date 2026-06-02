param(
    [string]$DistRoot = "dist\GameVoiceTranslator"
)

$internal = Join-Path $DistRoot "_internal"
$models = Join-Path $internal ".models"

$repositories = @(
    @{
        Name = "models--Systran--faster-whisper-base"
        Snapshot = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
        Files = @{
            "config.json" = "867cf1a0fece1394e01d55e287ba2f09a577c046"
            "model.bin" = "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"
            "tokenizer.json" = "7818adb6de9fa3064d3ff81226fdd675be1f6344"
            "vocabulary.txt" = "c9074644d9d1205686f16d411564729461324b75"
        }
    },
    @{
        Name = "models--Systran--faster-whisper-tiny"
        Snapshot = "d90ca5fe260221311c53c58e660288d3deb8d356"
        Files = @{
            "config.json" = "3baa18e2b321a2f489614607852a729fcd516480"
            "model.bin" = "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"
            "tokenizer.json" = "7818adb6de9fa3064d3ff81226fdd675be1f6344"
            "vocabulary.txt" = "c9074644d9d1205686f16d411564729461324b75"
        }
    }
)

foreach ($repo in $repositories) {
    $repoRoot = Join-Path $models $repo.Name
    $snapshotRoot = Join-Path $repoRoot ("snapshots\" + $repo.Snapshot)
    if (!(Test-Path $snapshotRoot)) {
        continue
    }

    foreach ($entry in $repo.Files.GetEnumerator()) {
        $target = Join-Path $snapshotRoot $entry.Key
        $source = Join-Path $repoRoot ("blobs\" + $entry.Value)
        if (!(Test-Path $source)) {
            throw "Missing Whisper blob: $source"
        }
        Remove-Item $target -Force -ErrorAction SilentlyContinue
        Copy-Item $source $target -Force
    }
}

Write-Output "Whisper cache links materialized."
