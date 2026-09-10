# 文档

自部署 **Douyin_TikTok_Download_API v5** 所需的全部内容：部署、使用、运维，以及参与开发。

**English documentation: [README.md](./README.md)** —— 这里的每一页都有中英两个版本，内容完全一致。

<div align="center">
    <img src="../screenshots/console-zh.gif" width="860" alt="DTK 控制台"/>
</div>

---

## 从这里开始

如果你是第一次运行它，按顺序读这三篇。大约半小时，读完你会有一个能跑起来、并且你看得懂的实例。

| | 页面 | 你会得到什么 |
|---|---|---|
| 1 | [快速开始](./zh/01-quickstart.md) | 单机跑起整套服务、创建管理员、成功解析第一条链接——先在控制台里，再用 `curl` |
| 2 | [核心概念](./zh/04-concepts.md) | 心智模型：身份、签名、调度、任务。读完你能在发出请求之前就预判它会发生什么 |
| 3 | [控制台总览](./zh/05-console-overview.md) | 界面怎么逛，以及告诉你实例是否健康的那四个页面 |

## 按你想做的事查

**部署与配置**

- [安装与部署](./zh/02-installation.md) —— 逐个服务讲清 compose 文件、全部 `DTK_*` 环境变量、profile、反向代理、升级、不用 Docker 怎么跑
  - 先选路线：[走哪条路](./zh/02-installation.md#走哪条路) —— Docker 和手动部署各自要付出什么
  - 用 Docker（推荐）：[首次安装](./zh/02-installation.md#首次安装)
  - 手动装依赖：[开发方式](./zh/02-installation.md#不用-docker-运行) · [裸机生产部署](./zh/02-installation.md#在裸机上做生产部署)
  - 机器在中国大陆：[中国大陆的网络准备](./zh/02-installation.md#中国大陆的网络准备) —— 先换源，不然多半卡在拉镜像
- [配置参考](./zh/03-configuration.md) —— 两层配置的区别，以及全部 54 项运行时配置的默认值和该在什么时候改
- [安全](./zh/15-security.md) —— 软件已经替你做了什么、什么是你自己的责任，以及哪些操作会交出凭据

**使用控制台**

- [控制台总览](./zh/05-console-overview.md) —— 外壳、总览、系统信息、日志、诊断
- [身份与代理](./zh/06-identities-and-proxies.md) —— 身份池及其状态机、铸造、导入 Cookie、代理、调度器页面与接口访问控制页面、熔断器
- [调试台与工具](./zh/07-playground-and-tools.md) —— 在浏览器里直接调任意接口、计算签名、反解签名、解析链接、按需铸造身份
- [下载、资料库与关注列表](./zh/08-downloads-and-library.md) —— 把媒体存到自己的硬盘上、几个月后还能找回来、定时采集某个目标
- [用户与 API 密钥](./zh/09-users-and-api-keys.md) —— 角色、权限范围、密钥，以及谁能做什么

侧边栏里的每一行都由上面某一篇负责，[控制台总览](./zh/05-console-overview.md#每个页面由哪篇文档负责)里就是那张对照表：调度器（`/scheduler`）和接口访问控制（`/endpoint-access`）在《身份与代理》里讲，备份（`/backup`）、通知（`/notifications`）和设置（`/settings`）在《运维》里讲。

**基于它开发**

- [REST API 指南](./zh/11-api.md) —— 鉴权、统一信封、异步模型与 `?wait=`、翻页、错误处理，附三种语言的完整客户端示例
- [MCP 与 AI 客户端](./zh/12-mcp.md) —— 把 Claude Code、Claude Desktop、Codex 或 Cherry Studio 指向你自己的实例
- [命令行参考](./zh/13-cli.md) —— 每一个 `dtk` 命令和选项

**让它一直跑下去**

- [运维](./zh/10-operations.md) —— 设置页、备份页与通知页：备份与恢复演练、告警渠道、数据保留、监控、容量规划、安全升级
- [故障排查](./zh/14-troubleshooting.md) —— 症状、原因、修复，附完整错误码对照表
- [常见问题与术语表](./zh/17-faq.md) —— 部署前后最常被问到的问题，以及本文档用到的每个术语

**修改代码**

- [参与开发](./zh/16-contributing.md) —— 开发环境、仓库结构、测试分层、一次改动必须通过的质量门禁

## 全部文档

| | 页面 | |
|---|---|---|
| 01 | [快速开始](./zh/01-quickstart.md) | 从零到第一次成功请求 |
| 02 | [安装与部署](./zh/02-installation.md) | 两种部署方式、compose、环境变量、profile、升级 |
| 03 | [配置参考](./zh/03-configuration.md) | 全部 54 项运行时配置 |
| 04 | [核心概念](./zh/04-concepts.md) | 心智模型 |
| 05 | [控制台总览](./zh/05-console-overview.md) | 外壳、总览、系统信息、日志、诊断 |
| 06 | [身份与代理](./zh/06-identities-and-proxies.md) | 身份池与调度器 |
| 07 | [调试台与工具](./zh/07-playground-and-tools.md) | 调试台、基础工具、接口文档 |
| 08 | [下载、资料库与关注列表](./zh/08-downloads-and-library.md) | 把内容留下来 |
| 09 | [用户与 API 密钥](./zh/09-users-and-api-keys.md) | 访问控制 |
| 10 | [运维](./zh/10-operations.md) | 上线之后的日子 |
| 11 | [REST API 指南](./zh/11-api.md) | 怎么调这个 HTTP API |
| 12 | [MCP 与 AI 客户端](./zh/12-mcp.md) | AI 客户端接入 |
| 13 | [命令行参考](./zh/13-cli.md) | `dtk` 命令 |
| 14 | [故障排查](./zh/14-troubleshooting.md) | 出问题的时候 |
| 15 | [安全](./zh/15-security.md) | 威胁模型与责任划分 |
| 16 | [参与开发](./zh/16-contributing.md) | 动代码 |
| 17 | [常见问题与术语表](./zh/17-faq.md) | 问题与术语 |

## 这里没有什么

**接口参考手册。** 每个接口、每个参数、每条约束和每种响应结构，都是从真正处理请求的那份代码生成的，因此它永远不会过时，也不存在一份会变旧的副本。你自己的实例在控制台里的 `/docs` 提供它，另外还有 `/swagger`、`/redoc` 和 `/openapi.json`——这四个地址都无需登录即可访问。中英双语：在地址后追加 `?lang=zh`。

[REST API 指南](./zh/11-api.md)是那份参考手册的补充——它讲的是一张接口清单讲不了的东西，比如 `202` 到底意味着什么、什么时候重试才有用。

## 关于准确性

这些页面上的每一条命令、每一个配置名、每一个默认值、每一条接口路径和每一个错误码，都逐一对照过它所描述的源码。如果你发现有哪一处是错的，那就是一个值得上报的 bug——请[提一个 issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)，说明是哪一页的哪一行。

## 获取帮助

- [Issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues) —— 公开、有记录，遇到过同样问题的人也能回答你
- 邮箱 `Evil0ctal1985@gmail.com` —— 直达作者本人，适合不方便公开的内容

请先读[故障排查](./zh/14-troubleshooting.md)，并在反馈时附上诊断页面或 `dtk diagnose` 的输出。它能回答维护者原本要反过来问你的大部分问题。如果服务根本起不来，就没有 api 容器可以跑这次自检，两条路都给不出结果——请直接说明这一点，并改为附上 `docker compose -p dtk -f docker/compose.yml logs` 的输出。
