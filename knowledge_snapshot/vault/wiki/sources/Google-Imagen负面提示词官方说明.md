---
title: "资料摘要：Google Imagen 负面提示词官方说明"
type: source
created: 2026-08-26
updated: 2026-08-26
tags: [提示词, 负面提示词, Imagen, Google, 官方文档]
source_url: "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/image/omit-content-using-a-negative-prompt"
source_type: official-documentation
read_date: 2026-08-26
---

# 资料摘要：Google Imagen 负面提示词官方说明

Google Cloud 官方文档把正文提示词与独立 Negative Prompt 字段分开：正文描述要生成的内容，负面字段描述要省略的元素。官方示例同时指出，在正文中写“without people”可能仍激活 people，建议把目标场景写入正文、把 `people` 放入专用负面字段。

该能力不是所有版本通用。文档标明旧版 Imagen 3 的部分模型支持 `negativePrompt`，从 `imagen-3.0-generate-002` 起的后续模型不再提供该旧功能。因此生产规则必须先核对具体模型能力，不能把独立负面字段视为所有图片模型的固定能力。

本来源支持的执行结论：**正文正向优先；专用负面字段按模型能力使用；两种通道不得混为一谈。**
