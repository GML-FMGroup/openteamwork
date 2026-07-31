# OpenPPX Desktop

OpenPPX Desktop 是 OpenPPX monorepo 中的桌面客户端，支持管理本机 OpenPPX Node，也支持连接可信局域网中另一台机器上的 Node。

当前桌面版本为 `0.5.0-beta.1` Developer Preview。它是前后端分离的薄客户端：安装包只包含 Desktop，不包含 Python 后端、Agent 配置或用户数据。

当前版本聚焦这几条主路径：

- runtime 状态
- agent 列表
- session 列表
- 对话工作区
- 富文本消息渲染
- 本地 `client-api` + SSE 事件流
- 带 Token 认证的局域网 Node 连接

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
3. 通过受保护的 `/api/v1/node` 验证认证、Node 身份和 capability
4. gateway 不可用、未授权或协议不兼容时显式显示错误

客户端不会再自动展示 mock 数据，也不会静默回退到 legacy Python bridge。两者只保留为显式的开发调试模式。

## 开发前置条件

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

## 构建 Developer Preview

当前打包目标是未签名的 macOS Apple Silicon（arm64）Developer Preview。

先构建可直接检查的 `.app`：

```bash
pnpm desktop:package:dir
```

再生成 DMG：

```bash
pnpm desktop:package
pnpm desktop:checksum
```

产物位于 `apps/desktop/release/`，其中包括：

- `OpenPPX-Desktop-0.5.0-beta.1-mac-arm64.dmg`
- 对应的 `.blockmap`
- `SHA256SUMS.txt`

可在发布前验证 DMG 和校验清单：

```bash
hdiutil verify apps/desktop/release/OpenPPX-Desktop-0.5.0-beta.1-mac-arm64.dmg
cd apps/desktop/release
shasum -a 256 -c SHA256SUMS.txt
```

`release/` 是本地构建目录，不进入 Git。当前候选没有 Apple Developer ID 签名和 notarization，因此 macOS 可能显示来源验证提示；它适合开发者测试，不应被描述成正式稳定版。

安装 Desktop 后仍需单独运行 OpenPPX Node。Desktop 可以连接同一台机器上的 Node，也可以连接可信局域网中另一台机器上的 Node。

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

### 2. 局域网模式

在远端机器生成随机 Token，并启动局域网监听：

```bash
export OPENPPX_CLIENT_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Copy this Token into Desktop: %s\n' "$OPENPPX_CLIENT_API_TOKEN"
ppx client-api serve --host 0.0.0.0 --port 8765
```

没有 `OPENPPX_CLIENT_API_TOKEN` 时，非回环监听会拒绝启动。

然后在 Desktop 设置页配置：

- 运行位置：`连接局域网 OpenPPX Node`
- Node 名称
- Gateway URL，例如 `http://192.168.1.20:8765`
- Access Token

先点击“测试连接”，确认 Node 身份、版本和认证状态，再保存。Token 由 Electron Main 使用 `safeStorage` 加密保存；普通 `connection-settings.json` 中的凭证位置只保存 `secretRef`，不会保存明文 Token。

开发时也可以完全使用环境变量：

```bash
export OPENPPX_TARGET_TYPE=lan
export OPENPPX_TARGET_NAME="Studio Node"
export OPENPPX_CLIENT_API_BASE_URL=http://10.0.0.8:8765
export OPENPPX_CLIENT_API_TOKEN="<same-random-secret>"
pnpm desktop:dev
```

当前局域网模式：

- 普通 HTTP 和 SSE 使用同一 Bearer Token
- 不会再尝试本地拉起 `client-api`
- 不会再 fallback 到本地 bridge
- 不会把 Token 返回给 Renderer diagnostics 或写入普通 JSON

当前还没完成：

- 多 target 管理
- 自动发现与配对
- TLS 自动配置、SSH tunnel、Tailnet 和公网 Relay

> 当前模式只适用于可信局域网。不要把 Client API 端口直接转发到公网。

## 设置页当前能做什么

设置页现在已经支持：

- 查看 runtime diagnostics
- 查看当前 target
- 查看 gateway URL 和连接状态
- 切换 `local / lan`
- 编辑 target 名称
- 编辑 gateway URL
- 测试 Token、协议版本和 Node 身份
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

legacy bridge 只允许本地 target；LAN target 永远不会使用它。正式使用和发布构建不应设置这两个变量。

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

### 3. 启动后显示本地 Node 离线

正式构建不会自动回退到 mock。优先检查：

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
- 自动发现与配对
- 更完整的 tool / attachment 展示
- TLS、SSH tunnel、Tailnet 或公网 Relay 等远程连接方式

## 说明

- `task_plan.md`、`findings.md`、`progress.md` 是本地规划文件，不应提交。
- Desktop 与 Python/Google ADK 后端位于同一仓库，但仍通过 HTTP/SSE 保持前后端分离。
