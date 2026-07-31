# OpenPPX Desktop

OpenPPX Desktop 是 OpenPPX monorepo 中的桌面客户端，当前以本地模式为主，同时已经补了远程 gateway 的初步接入准备。

当前版本聚焦这几条主路径：

- runtime 状态
- agent 列表
- session 列表
- 对话工作区
- 富文本消息渲染
- 本地 `client-api` + SSE 事件流
- 远程 target 的基础连接配置

## 架构

```text
Renderer (React)
  -> preload host API
  -> Electron main
  -> openppx local adapter
  -> versioned openppx client-api gateway
```

当前优先级顺序是：

1. 使用 `openppx client-api` HTTP + SSE
2. 通过 `/api/v1/health` 验证 Client API v1 兼容性
3. gateway 不可用或协议不兼容时显式显示错误

客户端不会再自动展示 mock 数据，也不会静默回退到 legacy Python bridge。两者只保留为显式的开发调试模式。

## 前置条件

- 已安装 `Node.js`
- 已安装 `pnpm`
- 已在 OpenPPX 仓库根目录安装 Python 后端
- 推荐本地已经执行过 `ppx init`

如果你要跑真实本地模式，通常还需要：

- `~/.openppx/global_config.json`
- 至少一个 enabled agent
- 仓库根目录的 `.venv`

## 安装依赖

在仓库根目录执行：

```bash
pnpm install
```

如果 `pnpm` 提示忽略了 `electron` 或 `esbuild` 的 build scripts，需要再执行一次：

```bash
pnpm approve-builds
pnpm install
```

这一步很重要，否则 Electron 可能会报安装不完整。

## 启动前端

开发模式：

```bash
pnpm desktop:dev
```

生产构建：

```bash
pnpm desktop:build
```

测试：

```bash
pnpm desktop:test
```

## 配合后端启动

OpenPPX Desktop 最理想的工作方式，是本地 `openppx client-api` 已经在跑。在仓库根目录执行：

```bash
source .venv/bin/activate
ppx client-api serve --host 127.0.0.1 --port 8765
```

然后在另一个终端的仓库根目录启动 Desktop：

```bash
pnpm desktop:dev
```

如果本地 target 处于默认配置，客户端也会尝试按需拉起本地 `client-api`。

但开发联调时，还是建议你先手动启动 `client-api`，这样日志更清楚，也更方便排查问题。

Client API v1 的握手、兼容性规则和共享测试 fixtures 见 [`contracts/client-api/`](../../contracts/client-api/README.md)。

## 常见启动方式

### 1. 本地模式

默认就是本地模式。

默认会从 monorepo 根目录发现后端。如果要连接另一份本地 OpenPPX 源码，先设置：

```bash
export OPENPPX_ROOT=/path/to/openppx_root
```

然后：

```bash
pnpm desktop:dev
```

### 2. 远程模式初版

当前已经支持一个“可改的初版”远程连接方式。

你可以在设置页里直接配置：

- `Target type`: `remote`
- `Target name`
- `Gateway URL`

也可以用环境变量先指定：

```bash
export OPENPPX_TARGET_TYPE=remote
export OPENPPX_TARGET_NAME="Ops Gateway"
export OPENPPX_CLIENT_API_BASE_URL=http://10.0.0.8:8765
pnpm desktop:dev
```

当前远程模式的范围是：

- 可以连接远程 gateway
- 不会再尝试本地拉起 `client-api`
- 不会再 fallback 到本地 bridge

当前还没完成的远程能力：

- 多 target 管理
- 认证 token / api key
- 完整远端部署指引

## 设置页当前能做什么

设置页现在已经支持：

- 查看 runtime diagnostics
- 查看当前 target
- 查看 gateway URL 和连接状态
- 切换 `local / remote`
- 编辑 target 名称
- 编辑 gateway URL
- 保存并应用连接配置

连接配置会持久化到 Electron 用户目录，下次打开客户端会继续使用。

## 调试

### 显式开发模式

只有在开发示例界面时才启用 mock：

```bash
OPENPPX_DESKTOP_MOCK=1 pnpm desktop:dev
```

只有在排查旧链路时才启用 legacy bridge：

```bash
OPENPPX_DESKTOP_LEGACY_BRIDGE=1 pnpm desktop:dev
```

legacy bridge 只允许本地 target；远程 target 永远不会使用它。正式使用和发布构建不应设置这两个变量。

客户端调试日志：

```bash
OPENPPX_CLIENT_DEBUG=1 pnpm desktop:dev
```

后端 `client-api` 调试日志：

```bash
source .venv/bin/activate
OPENPIPIXIA_DEBUG=1 ppx client-api serve --host 127.0.0.1 --port 8765
```

如果需要把后端日志写入文件：

```bash
OPENPIPIXIA_DEBUG=1 OPENPIPIXIA_DEBUG_LOG_PATH=/tmp/openppx-client-api-debug.log ppx client-api serve --host 127.0.0.1 --port 8765
```

## 常见问题

### 1. Electron failed to install correctly

通常是因为 `pnpm` 忽略了 build scripts。按下面顺序处理：

```bash
pnpm approve-builds
pnpm install
```

### 2. 只有空白页、黄底页、或者 preload 注入失败

这类问题通常是：

- Electron 依赖没装完整
- preload 产物不对
- dev 进程没重启

先尝试：

```bash
pnpm install
pnpm desktop:dev
```

如果之前改过 Electron 相关代码，记得先停掉旧的 dev 进程再重启。

### 3. 启动后进入 mock mode

说明客户端没有找到可用的本地 `openppx` 运行环境。优先检查：

- `OPENPPX_ROOT` 是否正确
- `openppx_root/.venv` 是否存在
- `~/.openppx/global_config.json` 是否存在
- 是否至少有一个 enabled agent

### 4. 本地模式下发送消息失败

建议先单独启动后端：

```bash
source .venv/bin/activate
ppx client-api serve --host 127.0.0.1 --port 8765
```

然后再开客户端。这样最容易看出是 gateway 问题、agent 配置问题，还是前端问题。

## 当前实现范围说明

第一版已经具备：

- 本地 agent 发现
- 本地 session/history 读取
- 本地消息发送
- 结构化 step 卡片
- 失败态 / 取消态展示
- 设置页诊断和连接配置

但当前还属于“第一版可用”阶段，后面还会继续补：

- 多 target 管理
- 远端认证
- 更完整的 tool / attachment 展示
- 更正式的 remote gateway 使用方式

## 说明

- `task_plan.md`、`findings.md`、`progress.md` 是本地规划文件，不应提交。
- Desktop 与 Python/Google ADK 后端位于同一仓库，但仍通过 HTTP/SSE 保持前后端分离。
