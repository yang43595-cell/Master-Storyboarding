# validate-project.ps1
# 基础项目结构和引用路径校验脚本
# 用法: powershell.exe -NoProfile -ExecutionPolicy Bypass -File validate-project.ps1 -ProjectPath <桌面项目文件夹>
# 文件使用 UTF-8 BOM，兼容 Windows PowerShell 5.1 的中文字符串解析。
# 注意: 脚本通过不代表创意质量通过，仍需按各 skill 的验收清单自检

param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectPath
)

$ErrorActionPreference = "Stop"

function Add-Issue {
  param(
    [string]$Level,
    [string]$Message
  )
  [PSCustomObject]@{
    Level   = $Level
    Message = $Message
  }
}

# 目录名常量
$dirManage            = "项目管理"
$dirScriptStoryboard  = "脚本与分镜文档"
$dirOfficialImages    = "正式分镜图"
$dirExecutionDocs     = "Seedance执行文档"
$dirVideos            = "视频_正式片段"
$dirReferences        = "参考帧与临时素材"
$dirArchive           = "历史与废弃"

# project_state.md 中必须包含的节标题
$stateCurrentStage    = "当前阶段"
$stateActiveAssets    = "当前有效资产"
$stateProductRefs     = "产品参考图"
$stateStoryboardList  = "分镜图清单"
$stateSegmentList     = "Seedance 段落清单"

$issues = New-Object System.Collections.Generic.List[object]
$root = Resolve-Path -LiteralPath $ProjectPath -ErrorAction Stop
$projectRoot = $root.Path

# 新项目按生产流程排列，未迁移项目兼容旧结构。
$isWorkflowLayout = Test-Path -LiteralPath (Join-Path $projectRoot "02_编导") -PathType Container
if ($isWorkflowLayout) {
  $requiredDirs = @("00_项目管理", "01_原始资料", "02_编导", "03_导演", "04_资产", "05_分镜", "06_视频", "99_历史与废弃")
  $imageRelative = "05_分镜"
  $executionRelative = "06_视频"
  $referenceRelative = "04_资产"
  $voiceRelative = "07_声音与配音"
} else {
  $requiredDirs = @("00_项目管理", "01_脚本与分镜文档", "02_正式分镜图", "03_Seedance执行文档", "04_视频_正式片段", "06_参考帧与临时素材", "99_历史与废弃")
  $imageRelative = "02_正式分镜图"
  $executionRelative = "03_Seedance执行文档"
  $referenceRelative = "06_参考帧与临时素材"
  $voiceRelative = "05_声音与配音"
}

foreach ($dir in $requiredDirs) {
  $path = Join-Path $projectRoot $dir
  if (-not (Test-Path -LiteralPath $path -PathType Container)) {
    $issues.Add((Add-Issue "ERROR" "Missing directory: $dir"))
  }
}

# 角色声音参考为可选能力；项目存在清单时执行专用校验，不要求旧项目补空目录。
$voiceManifestPath = Join-Path $projectRoot "$voiceRelative/voice_reference_manifest.json"
if (Test-Path -LiteralPath $voiceManifestPath -PathType Leaf) {
  $voiceValidator = Join-Path $PSScriptRoot "voice_reference_cli.py"
  if (-not (Test-Path -LiteralPath $voiceValidator -PathType Leaf)) {
    $issues.Add((Add-Issue "ERROR" "voice_reference_cli.py not found: $voiceValidator"))
  } else {
    $voiceOutput = @(& python $voiceValidator validate-project --project $projectRoot 2>&1)
    if ($LASTEXITCODE -ne 0) {
      foreach ($line in $voiceOutput) {
        $issues.Add((Add-Issue "ERROR" "voice reference validation: $line"))
      }
    }
  }
}

# 结构化状态固定门禁：新项目缺 project_state.json 报 ERROR，legacy 项目降 WARN。
$structuredStateMode = "enforce_new_warn_legacy"

# 项目模式：读 00_项目管理/project_request.json（缺省 new）。resume 项目保留 legacy 兼容。
$projectMode = "new"
$requestJsonPath = Join-Path $projectRoot "00_$dirManage\project_request.json"
if (Test-Path -LiteralPath $requestJsonPath -PathType Leaf) {
  try {
    $requestJson = Get-Content -LiteralPath $requestJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($requestJson.project_mode) {
      $projectMode = [string]$requestJson.project_mode
    }
  } catch {
    # project_request.json 解析失败按 new 处理
  }
}

# 检查项目状态：优先校验 project_state.json，旧项目仅有 Markdown 时按 legacy 处理。
$stateJsonPath = Join-Path $projectRoot "00_$dirManage\project_state.json"
$statePath      = Join-Path $projectRoot "00_$dirManage\project_state.md"
$hasStateJson   = Test-Path -LiteralPath $stateJsonPath -PathType Leaf
$hasStateMd     = Test-Path -LiteralPath $statePath -PathType Leaf

if ($hasStateJson) {
  # 结构化状态存在：校验现行 stage-1.0 最小事实合同与 Markdown 投影。
  try {
    $stateJson = Get-Content -LiteralPath $stateJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($requiredField in @("schema_version", "current_stage", "current_artifacts", "user_confirmations", "review_report", "accepted_issues", "next_stage")) {
      if ($stateJson.PSObject.Properties.Name -notcontains $requiredField) {
        $issues.Add((Add-Issue "ERROR" "project_state.json missing required field: $requiredField"))
      }
    }
    if ($stateJson.schema_version -ne "stage-1.0") {
      $issues.Add((Add-Issue "ERROR" "project_state.json schema_version must be stage-1.0, got: $($stateJson.schema_version)"))
    }
    if ($hasStateMd) {
      # JSON 与投影的当前阶段、修订必须一致，防止投影与事实脱节。
      $stateText = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8
      foreach ($requiredText in @("当前阶段：", "当前有效产物：", "用户确认：", "审核报告：", "已接受问题：", "下一阶段：")) {
        if ($stateText -notmatch [regex]::Escape($requiredText)) {
          $issues.Add((Add-Issue "WARN" "Optional state projection missing section: $requiredText"))
        }
      }
      $stageText = [regex]::Match($stateText, '当前阶段[:：]\s*\S+').Value
      if ($stageText -and $stateJson.current_stage -and ($stageText -notmatch [regex]::Escape([string]$stateJson.current_stage))) {
        $issues.Add((Add-Issue "WARN" "project_state.md stage does not match project_state.json: $stageText"))
      }
    }
  } catch {
    $issues.Add((Add-Issue "ERROR" "project_state.json is not valid JSON: $($_.Exception.Message)"))
  }
} elseif ($hasStateMd) {
  # 旧版状态：resume 项目保留兼容（历史项目未迁移）；新项目缺 project_state.json 违反结构化门禁。
  if ($projectMode -ne "resume" -and $structuredStateMode -eq "enforce_new_warn_legacy") {
    $issues.Add((Add-Issue "ERROR" "new project requires project_state.json (project_mode=$projectMode); run inspect-project to migrate"))
  } else {
    $issues.Add((Add-Issue "WARN" "legacy state: 00_$dirManage/project_state.md without project_state.json; run inspect-project to migrate"))
  }
  $stateText = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8
  foreach ($requiredText in @($stateCurrentStage, $stateActiveAssets, $stateProductRefs, $stateStoryboardList, $stateSegmentList)) {
    if ($stateText -notmatch [regex]::Escape($requiredText)) {
      $issues.Add((Add-Issue "WARN" "State file missing section: $requiredText"))
    }
  }
} else {
  $issues.Add((Add-Issue "ERROR" "Missing state file: 00_$dirManage/project_state.md and project_state.json"))
}

# 资产状态与物理目录必须一致：过期 / 废弃 / 失败素材不能留在活跃参考目录。
# 只匹配“当前有效资产”表中的当前布局资产目录，避免误扫历史说明。
if ($hasStateMd) {
  $activeReferencePrefix = "$referenceRelative/"
  $activeReferencePattern = '(?m)^\|\s*[^|]+\|\s*`?(?<path>' + [regex]::Escape($activeReferencePrefix) + '[^`|]+)`?\s*\|\s*(?<status>[^|]+)\|'
  $activeReferenceRows = [regex]::Matches($stateText, $activeReferencePattern)
  foreach ($row in $activeReferenceRows) {
    $assetPath = $row.Groups["path"].Value.Trim()
    $assetStatus = $row.Groups["status"].Value.Trim()
    if ($assetStatus -match '过期|废弃|失败') {
      $issues.Add((Add-Issue "ERROR" "Expired asset is still in active reference directory: $assetPath (status: $assetStatus)"))
    }
  }
}

# 检查正式分镜图目录是否只含 PNG
$officialImageDir = Join-Path $projectRoot $imageRelative
if (Test-Path -LiteralPath $officialImageDir -PathType Container) {
  $nonPngFiles = Get-ChildItem -LiteralPath $officialImageDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension.ToLowerInvariant() -ne ".png" }
  foreach ($file in $nonPngFiles) {
    $issues.Add((Add-Issue "WARN" "Non-PNG file in $imageRelative`: $($file.Name)"))
  }
}

# 检查执行文档中的模糊时长和引用图片路径
$executionDir = Join-Path $projectRoot $executionRelative
if (Test-Path -LiteralPath $executionDir -PathType Container) {
  $docs = Get-ChildItem -LiteralPath $executionDir -File -Include *.md,*.txt -Recurse -ErrorAction SilentlyContinue
  foreach ($doc in $docs) {
    $text = Get-Content -LiteralPath $doc.FullName -Raw -Encoding UTF8

    # 检查生成时长列是否模糊（约/大概/建议/区间），不再扫描全文档的"约×秒"，
    # 避免把"目标成片约30秒"这类成片目标时长误报为单段生成时长模糊。
    # 生成时长单元格必须含数字；像音频表"| S01 | 有台词 | 模型语音 | 必须 | … |"这种
    # 同样以 S\d+ 开头的表格不会被误当成生成时长表。
    $durationRowPattern = '(?m)^\|\s*(?<seg>S\d+)\s*\|[^|]*\|[^|]*\|(?<dur>[^|\d]*\d[^|]*)\|'
    foreach ($durationMatch in [regex]::Matches($text, $durationRowPattern)) {
      $dur = $durationMatch.Groups["dur"].Value.Trim()
      if ($dur -match '约|大概|建议|~\s*|\d+\s*-\s*\d+') {
        $issues.Add((Add-Issue "WARN" "Execution doc fuzzy generation duration for $($durationMatch.Groups['seg'].Value): $dur ($($doc.Name))"))
      } elseif ($dur -notmatch '^\s*\d+\s*秒\s*$') {
        $issues.Add((Add-Issue "WARN" "Execution doc generation duration is not an integer second value for $($durationMatch.Groups['seg'].Value): '$dur' ($($doc.Name))"))
      }
    }

    # 通用图片引用在下方 Python --include-images 中检查，覆盖 JSON/MD/TXT。
  }

  # 产品类型只认明确标记或已确认资产类别；普通剧情道具名称不触发广告产品门禁。
  $previousOutputEncoding = [Console]::OutputEncoding
  $previousOutputEncoder = $OutputEncoding
  try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
    try {
      $productOutput = @(& python -m scripts.core.product_refs --project $projectRoot --directory $executionDir --include-images 2>&1)
      $productExit = $LASTEXITCODE
    } finally {
      Pop-Location
    }
    if ($productExit -ne 0) {
      foreach ($line in $productOutput) {
        $issues.Add((Add-Issue 'ERROR' "Product reference validation: $line"))
      }
      if ($productOutput.Count -eq 0) {
        $issues.Add((Add-Issue 'ERROR' "Product reference validation failed (exit $productExit)"))
      }
    }
  } finally {
    [Console]::OutputEncoding = $previousOutputEncoding
    $OutputEncoding = $previousOutputEncoder
  }
}

# production_plan.json 存在时，把语义预检纳入项目门禁。
# 只读 shadow 模式，不在此处写文件；ERROR 即阻塞提交。
$preflightPlanPath = Join-Path $projectRoot "00_$dirManage\production_plan.json"
if (Test-Path -LiteralPath $preflightPlanPath -PathType Leaf) {
  $pyScript = Join-Path $PSScriptRoot "validate-production-plan.py"
  if (-not (Test-Path -LiteralPath $pyScript -PathType Leaf)) {
    $issues.Add((Add-Issue "ERROR" "validate-production-plan.py not found: $pyScript"))
  } else {
    # Python 子进程写 UTF-8；Windows PowerShell 5.1 默认按 GBK 解码会乱码，临时切 UTF-8 再还原。
    $previousOutputEncoding = [Console]::OutputEncoding
    $previousOutputEncoder = $OutputEncoding
    try {
      [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
      $OutputEncoding = [System.Text.Encoding]::UTF8
      $pyOutput = @(& python $pyScript --project-path $projectRoot --mode shadow 2>&1)
    } finally {
      [Console]::OutputEncoding = $previousOutputEncoding
      $OutputEncoding = $previousOutputEncoder
    }
    $pyExit = $LASTEXITCODE
    if ($pyExit -ne 0) {
      foreach ($line in $pyOutput) {
        if ($line -match '^\[WARN\]') {
          $issues.Add((Add-Issue "WARN" "production plan preflight: $line"))
        } else {
          $issues.Add((Add-Issue "ERROR" "production plan preflight: $line"))
        }
      }
    } else {
      foreach ($line in $pyOutput) {
        if ($line -match 'WARN') {
          $issues.Add((Add-Issue "WARN" "production plan preflight: $line"))
        }
      }
    }
  }
}

if ($issues.Count -eq 0) {
  Write-Output "PASS: project structure validation passed."
  exit 0
}

$issues | Format-Table -AutoSize
if (@($issues | Where-Object { $_.Level -eq "ERROR" }).Count -gt 0) {
  exit 1
}
exit 0
