<#
    הרצת כל הבדיקות: יחידה, שכבות הבמאי, קצה-אל-קצה ו-Golden Video.
    הרצה:  powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
    הודעות הקונסולה באנגלית: קונסולת Windows מציגה עברית הפוכה.

    לא כלולות כאן (דורשות שרת או שידור מקומי, ומריצות אותו בעצמן):
      python tests\run_server_suites.py
      python tests\live_hls_lab.py
      python tests\site_walkthrough.py
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { $venvPy = "python" }

$testData = Join-Path $env:TEMP "polixor_testdata"
$env:POLIXOR_DATA_DIR = Join-Path $env:TEMP "polixor_testrun"

$suites = @(
    "test_units", "test_errors", "test_images_live",
    "test_timeline", "test_semantics", "test_hook_pacing",
    "test_director", "test_bridge", "test_captions",
    "test_mastering", "test_render_qa", "test_broll", "test_music",
    "test_lang", "test_subtitle_style", "test_longform", "test_ingest",
    "test_i18n_catalog", "test_access", "test_transcribe_audio", "test_clip_intel", "test_long_source", "test_asr_config", "test_transcript_correct", "test_caching", "test_acceptance_report", "test_subtitle_align", "test_transcribe_cloud", "test_performance", "test_ui_language", "test_notifications", "test_publishing", "test_publishing_ui", "test_youtube", "test_meta", "test_meta_ui", "test_tiktok", "test_tiktok_ui", "test_social_metadata", "test_social_metadata_ui"
)

$results = [ordered]@{}
Push-Location (Join-Path $root "backend")
try {
    foreach ($s in $suites) {
        Write-Host "`n=== $s ===" -ForegroundColor Cyan
        & $venvPy "tests\$s.py"
        $results[$s] = ($LASTEXITCODE -eq 0)
    }

    Write-Host "`n=== Building the test video ===" -ForegroundColor Cyan
    & $venvPy tests\make_test_video.py $testData
    $video = Join-Path $testData "polixor_test_stream.mp4"

    Write-Host "`n=== End-to-end pipeline ===" -ForegroundColor Cyan
    $env:POLIXOR_FIXTURE_TRANSCRIPT = Join-Path $testData "polixor_test_stream.transcript.json"
    & $venvPy tests\e2e_pipeline.py $video
    $results["e2e_pipeline"] = ($LASTEXITCODE -eq 0)

    Write-Host "`n=== Projects API ===" -ForegroundColor Cyan
    & $venvPy tests\test_projects.py $video
    $results["test_projects"] = ($LASTEXITCODE -eq 0)

    Write-Host "`n=== Facecam layouts ===" -ForegroundColor Cyan
    & $venvPy tests\make_reaction_video.py $testData
    & $venvPy tests\test_layout.py (Join-Path $testData "reaction_stream.mp4")
    $results["test_layout"] = ($LASTEXITCODE -eq 0)

    Write-Host "`n=== Golden Video (frame-level) ===" -ForegroundColor Cyan
    & $venvPy tests\golden_video.py
    $results["golden_video"] = ($LASTEXITCODE -eq 0)

    Write-Host "`n=== Summary ===" -ForegroundColor Cyan
    $failed = 0
    foreach ($k in $results.Keys) {
        if ($results[$k]) { Write-Host "  [OK]   $k" -ForegroundColor Green }
        else { Write-Host "  [FAIL] $k" -ForegroundColor Red; $failed++ }
    }
    if ($failed -gt 0) { exit 1 }
} finally {
    Pop-Location
}
