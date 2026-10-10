---
title: "资料摘要：即梦 CLI 体验指南"
aliases: [即梦CLI指南, 即梦CLI, dreamina-cli]
type: source
created: 2026-07-06
updated: 2026-07-12
tags: [即梦, cli, 自动化, agent]
source_url: https://bytedance.larkoffice.com/wiki/FVTwwm0bGiishxkKOoScdHR2nsg
source_type: clipping
read_date: 2026-07-12
---

# 即梦 CLI 体验指南

> 即梦 CLI 是一个面向 AGENT 的工具包，让 AGENT 能够使用即梦相关的生成能力。最新版本 v1.4.10（2026-06-26）。

## 基本信息

- **来源**：飞书文档（字节跳动）
- **最新版本**：v1.4.10（2026-06-26）
- **支持平台**：macOS、Linux、Linux arm64（Windows 可通过 WSL）

## 核心主张

1. **AGENT 可自动使用即梦账号进行生成任务**
2. **一键安装**：`curl -fsSL https://jimeng.jianying.com/cli | bash`
3. **核心命令**：text2image、text2video、image2image、image2video、frames2video、multiframe2video、multimodal2video、image_upscale、query_result、list_task
4. **多模态旗舰**：multimodal2video 支持同时输入图片 + 音频 + 提示词
5. **批量出图**（v1.4.10+）：`--generate_num` 参数支持 1-10 张批量生成
6. **4K 支持**：seedance2.0_vip 支持 4K 分辨率
7. **会话管理**：支持多对话工作空间（Session）
8. **自动更新**：CLI 启动时自动检测新版本

## V1.4.4 → v1.4.10 主要变化

| 版本 | 新增内容 |
|------|---------|
| v1.4.8 | seedance 2.0 mini 轻量模型 |
| v1.4.10 | 批量出图 `--generate_num`、seedance2.0_vip 4K、超清任务修复 |

## 关键用法

```bash
# 安装
curl -fsSL https://jimeng.jianying.com/cli | bash

# 登录（无头模式用于服务器）
dreamina login --headless

# 文生视频
dreamina text2video --prompt="..." --duration=5 --ratio=16:9 --poll=30

# 多模态视频（图片+音频+提示词）
dreamina multimodal2video --image ./ref.png --audio ./music.mp3 --prompt="..."

# 批量文生图（v1.4.10+）
dreamina text2image --prompt="..." --ratio=1:1 --generate_num=5 --poll=60

# 图片超分
dreamina image_upscale --image ./input.png --resolution_type=4k
```

## 模型参考

| 模型 | 用途 | 最高分辨率 |
|------|------|-----------|
| 3.0-5.0 | 文生图 | 4K |
| seedance2.0 | 视频 | 720p |
| seedance2.0_vip | 视频 | 4K |
| seedance2.0_mini | 轻量视频 | 720p |
| seedance2.0_fast_vip | 加速通道 | 1080p |

## 与现有知识的关系

- ➕ 补充：[[sources/seedance-官方手册]] 的编程化接口
- 🔗 使 [[entities/即梦]] 的能力可以嵌入到任何工作流中
- 🔗 与 [[concepts/03-AI生成与视频/ai分镜设计|AI 分镜设计]] 结合可实现"分镜 → 自动批量生成"的全自动化管线

## 引发的思考

- CLI 的发布意味着 AI 视频生成正在 API 化
- 批量出图 + 多模态视频使口播图文视频生产可以全自动化
- `--headless` 登录意味着可以在云服务器上运行
