# Downloads the USGS / JPL source rasters for Marswalk 3D into data/raw.
# Usage (from the marswalk-3d folder):  powershell -ExecutionPolicy Bypass -File scripts/prepare-terrain/download_data.ps1
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ctxBase = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/"
$hirBase = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/"

$files = @(
  @{ url = $ctxBase + "M20_JezeroCrater_CTXDEM_20m.tif";          dir = "data\raw\ctx" },
  @{ url = $ctxBase + "M20_JezeroCrater_CTXDEM_20m.xml";          dir = "data\raw\ctx" },
  @{ url = $ctxBase + "M20_JezeroCrater_CTXortho_mosaic_5m.tif";  dir = "data\raw\ctx" },
  @{ url = $ctxBase + "M20_JezeroCrater_CTXortho_mosaic_5m.xml";  dir = "data\raw\ctx" },
  @{ url = $hirBase + "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"; dir = "data\raw\hirise" },
  @{ url = $hirBase + "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.xml"; dir = "data\raw\hirise" },
  @{ url = $hirBase + "JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif"; dir = "data\raw\hirise" },   # 6.9 GB
  @{ url = $hirBase + "JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.xml"; dir = "data\raw\hirise" }
)

foreach ($f in $files) {
  $dir = Join-Path $root $f.dir
  New-Item -ItemType Directory -Force $dir | Out-Null
  $out = Join-Path $dir ([IO.Path]::GetFileName($f.url))
  if (Test-Path $out) { Write-Host "exists  $out"; continue }
  Write-Host "get     $($f.url)"
  curl.exe -sS -L --fail --retry 5 -C - -o $out $f.url
}
Write-Host "Done."
