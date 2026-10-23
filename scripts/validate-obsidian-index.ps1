# Architecture entry validation for Windows PowerShell 5.1.
#
# 动态知识由各 Skill 的 knowledge-profile.json 选择，本地载体由
# references/workflow/knowledge-entrypoints.json 登记。
# 不维护中央阶段表或参考路由表。
# 生产 Skill 只消费阶段启动包中的有界知识上下文；不得直接扫描 Wiki。
# enforce 模式下没有旧 Wiki 文件枚举；回退方式是回退 cutover commit。

param(
  [string]$ProjectRoot = "",
  [string]$WikiRoot = ""
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
if ([string]::IsNullOrWhiteSpace($WikiRoot)) {
  $WikiRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Obsidian Vault\wiki"
}

$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$wiki = (Resolve-Path -LiteralPath $WikiRoot).Path
$vaultRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $wiki)).Path
$issues = New-Object System.Collections.Generic.List[string]

function Require-File {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $issues.Add("Missing file: $Path") }
}

$configPath = Join-Path $project "skill-config.md"
$refsIndexPath = Join-Path $project "references\index.md"
Require-File $configPath
Require-File $refsIndexPath

# 1) 校验 profile 与入口登记存在；具体语义由确定性审计器统一检查。
$policyPath = Join-Path $project "references\workflow\knowledge-policy.json"
$entrypointRegistryPath = Join-Path $project "references\workflow\knowledge-entrypoints.json"
Require-File $policyPath
Require-File $entrypointRegistryPath
$skillsRoot = Join-Path $project "skills"
if (Test-Path -LiteralPath $skillsRoot -PathType Container) {
  foreach ($directory in Get-ChildItem -LiteralPath $skillsRoot -Directory) {
    if ($directory.Name -eq "_shared") { continue }
    $skillPath = Join-Path $directory.FullName "SKILL.md"
    if (Test-Path -LiteralPath $skillPath -PathType Leaf) {
      Require-File (Join-Path $directory.FullName "knowledge-profile.json")
    }
  }
}

# 2) 动态路由守卫：生产 Skill 与入口文档不得再内嵌具体 Wiki 页面或读取旧目录。
$guardFiles = @(
  "skill-config.md",
  "references\index.md",
  "references\prompt-guides.md",
  "skills\storyboard-director\templates\storyboard-image-template.md"
)
if (Test-Path -LiteralPath $skillsRoot -PathType Container) {
  foreach ($directory in Get-ChildItem -LiteralPath $skillsRoot -Directory) {
    if ($directory.Name -eq "_shared") { continue }
    $guardFiles += "skills\$($directory.Name)\SKILL.md"
  }
}
foreach ($relative in $guardFiles) {
  $path = Join-Path $project $relative
  Require-File $path
  if (Test-Path -LiteralPath $path -PathType Leaf) {
    $text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ($text -match '(?i)(?:[a-z]:|\\\\[^\\/`\r\n]+[\\/][^\\/`\r\n]+|\$(?:env:)?[a-z_][a-z0-9_]*|\$\{[a-z_][a-z0-9_]*\}|%[a-z_][a-z0-9_]*%)[^`\r\n]*?[\\/]wiki[\\/][^`\r\n]+\.md') {
      $issues.Add("Specific Wiki absolute path remains in manifest-routed file: $path")
    }
    if ($text -match '(?i)vault://wiki[\\/][^`\s]+\.md') {
      $issues.Add("Specific vault Wiki path remains in manifest-routed file: $path")
    }
    if ($text -match '(?im)(?:^|[`"''\s（(]|wiki\s*[：:]\s*)(?:wiki[\\/])?(?:concepts|synthesis|sources)[\\/][^`\r\n]+\.md') {
      $issues.Add("Specific relative Wiki page remains in manifest-routed file: $path")
    }
    if ($text -match 'catalog\s*(?:\[|\.)') {
      $issues.Add("Legacy knowledge catalog is read directly by manifest-routed file: $path")
    }
  }
}

# 3) 入口文件与旧短剧路径守卫（保留）。
$entryFiles = @(
  "PROJECT_RULES.md",
  "skill-config.md",
  "references\index.md",
  "skills\creative-director\SKILL.md",
  "skills\storyboard-director\SKILL.md",
  "skills\voiceover-to-video-script\SKILL.md",
  "skills\asset-image-assistant\SKILL.md"
)
foreach ($relative in $entryFiles) {
  $path = Join-Path $project $relative
  Require-File $path
  if (Test-Path -LiteralPath $path -PathType Leaf) {
    $text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ($text -match "Obsidian Vault[\\/]短剧|短剧[\\/]references|短剧[\\/]index") {
      $issues.Add("Legacy short-drama path remains in entry file: $path")
    }
  }
}

# 4) 可执行知识生产入口审计：动态规则、本地权威载体和动态范围外模板都不得静默失联。
$entrypointAudit = Join-Path $project "scripts\audit-knowledge-entrypoints.py"
$entrypointRegistry = Join-Path $project "references\workflow\knowledge-entrypoints.json"
Require-File $entrypointAudit
Require-File $entrypointRegistry
if ((Test-Path -LiteralPath $entrypointAudit -PathType Leaf) -and
    (Test-Path -LiteralPath $entrypointRegistry -PathType Leaf)) {
  $auditOutput = & python $entrypointAudit --vault $vaultRoot 2>&1
  if ($LASTEXITCODE -ne 0) {
    $issues.Add("Knowledge production entrypoint audit failed:`n$($auditOutput -join [Environment]::NewLine)")
  }
}

if ($issues.Count -eq 0) {
  Write-Output "PASS: wiki and local execution references are aligned."
  exit 0
}
$issues | ForEach-Object { Write-Output "ERROR: $_" }
exit 1
