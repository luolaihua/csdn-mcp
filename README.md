# CSDN MCP Server

让 AI 助手（如 Hermes、Claude、Cursor）直接发布文章到 CSDN（中国最大的技术社区），无需手动登录、复制粘贴。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-purple.svg)](https://modelcontextprotocol.io/)

## 架构

![CSDN MCP Server Architecture](architecture.png)

AI 助手通过 **MCP (Model Context Protocol)** 调用 CSDN MCP Server，后者使用 **Playwright** 驱动浏览器完成登录和发布操作。整个过程对用户透明——你只需扫码一次，后续所有发布都在后台自动完成。

## 功能

| 工具 | 说明 |
|------|------|
| `csdn_login` | 获取 CSDN 微信扫码登录二维码 |
| `csdn_confirm` | 确认扫码登录是否成功（自动检测回调） |
| `csdn_check_login` | 检查当前登录状态 |
| `csdn_publish` | 发布 Markdown 到 CSDN（默认存草稿，`draft=false` 发布） |

## 安装

```bash
# 克隆仓库
git clone git@github.com:luolaihua/csdn-mcp.git
cd csdn-mcp

# 安装依赖
pip install fastmcp playwright

# 安装 Chromium 浏览器
playwright install chromium
```

## 配置

在 Hermes 的 `config.yaml` 中添加：

```yaml
mcp_servers:
  csdn:
    command: fastmcp
    args:
      - run
      - /path/to/csdn-mcp/server.py:mcp
    timeout: 120
    connect_timeout: 30
```

然后 reload MCP：

```
/reload-mcp
```

## 使用

### 1. 扫码登录

```
让 AI 调用 csdn_login，扫描二维码并在手机上确认登录
```

### 2. 存草稿

```
AI 调用 csdn_publish，文章自动保存到 CSDN 草稿箱
```

### 3. 发布文章

```
AI 调用 csdn_publish(draft=false)，需用户确认后发布
```

### Markdown 格式

文章支持 frontmatter：

```markdown
---
title: 我的技术文章
tags: Python,AI,MCP
description: 文章摘要
---

## 正文内容

Markdown 格式的正文...
```

## 核心设计

### 持久化浏览器上下文

与传统方案不同，CSDN MCP Server 将 Playwright 浏览器实例**常驻在 MCP 进程中**：

- ✅ 一次扫码，登录态在进程生命周期内永久有效
- ✅ 所有 MCP 工具调用共享同一浏览器上下文
- ✅ 无需每次发布重新登录
- ✅ WSL/无头服务器环境完全兼容

### 线程安全的异步架构

使用 **async Playwright + asyncio** 彻底解决跨线程冲突（`cannot switch to a different thread`），所有浏览器操作在同一 event loop 线程执行。

### 草稿优先策略

- 默认 `csdn_publish(draft=true)`：只存草稿箱，不公开发布
- 发布需用户**显式确认**：`csdn_publish(draft=false)`
- 防止 AI 误操作直接发布未审核内容

## 技术栈

- **Python 3.10+**
- **FastMCP** — MCP 协议框架
- **Playwright (async)** — 浏览器自动化
- **asyncio** — 异步 IO

## 适用场景

| 场景 | 说明 |
|------|------|
| AI 写作 + CSDN 发布 | AI 生成博客后自动发布到 CSDN |
| 多平台同步 | 与微信公众号 MCP 配合实现一文多发 |
| 定时发布 | 配合 Hermes cron 定时发布内容 |
| CI/CD 文档发布 | 项目文档自动同步到 CSDN |

## 已知限制

- 需要用户在手机端微信扫码（首次登录）
- MCP 进程重启后需重新登录（浏览器上下文丢失）
- CSDN 编辑器更新可能导致选择器失效（需适配）

## License

MIT

## 作者

[luolaihua](https://github.com/luolaihua)
