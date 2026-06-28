# sbaiapi

<div align="center">

**轻量级上游 API 余额、分组倍率和令牌状态监控工具**

适合同时接入多个 `sub2api` / `new-api` 面板时，集中监控余额、倍率变化和令牌状态，并通过 QQBot 推送提醒。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-brightgreen)

快速开始 · 功能特性 · 支持面板 · 部署方式 · 安全提醒

</div>

## 项目介绍

`sbaiapi` 是一个偏实用的小工具，用来监控多个上游 API 站点：

- 余额是否不足。
- 令牌绑定的分组倍率是否变化。
- 分组是否涨价 / 降价。
- 涨价超过阈值时，是否需要自动禁用对应令牌。
- 通过 QQBot 查询当前状态或接收异常提醒。

它不是 API 网关，也不负责转发请求，只负责做上游状态监控。

## 预览

![QQ Bot status cards](docs/images/status-cards.png)

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 创建配置

```bash
cp config.example.json config.json
```

然后编辑：

```text
config.json
```

### 3. 手动检查一次

```bash
python monitor.py
```

Windows 可以双击：

```text
run_once.bat
```

### 4. 启动 QQ 查询机器人

```bash
python qqbot_listener.py
```

Windows 可以双击：

```text
start_qqbot.bat
```

给机器人发送：

```text
状态
```

机器人会返回最近一次监控结果。

## 功能特性

### 核心功能

- 多站点监控：一个配置文件管理多个上游站点。
- 余额监控：展示站点余额，余额过低时推送提醒。
- 分组倍率监控：自动读取令牌绑定分组，只监控实际用到的分组。
- 涨跌对比：和本地上一次记录对比，显示涨价或降价。
- 涨价自动禁用：涨价幅度超过阈值时，自动禁用对应分组令牌。
- QQBot 查询：发送 `状态` 查看缓存结果，避免频繁实时抓站。
- QQBot 卡片按钮：直接对分组令牌执行启用 / 禁用。

### 适用场景

- 同时接入多家上游 API 站点。
- 想知道余额还剩多少。
- 想及时发现上游分组倍率变化。
- 担心上游突然涨价导致成本异常。
- 想通过 QQ 单聊快速查看状态。

## 支持面板

### sub2api

项目地址：<https://github.com/Wei-Shaw/sub2api>

默认适配接口：

```text
/api/v1/auth/login
/api/v1/auth/me
/api/v1/groups/available
/api/v1/keys
```

### New API

项目地址：<https://github.com/QuantumNous/new-api>

默认适配接口：

```text
/api/user/login
/api/user/self
/api/pricing
/api/token
```

不同 fork 版本可能改过接口路径，如不兼容，需要按实际接口改一下脚本。

## 配置示例

### sub2api

```json
{
  "启用": true,
  "名称": "示例 sub2api",
  "类型": "subapi",
  "网址": "https://example-subapi.com",
  "邮箱": "your@example.com",
  "密码": "your-password",
  "token": "",
  "余额提醒线": 1,
  "涨价禁用阈值": 0.5,
  "时区": "Asia/Hong_Kong"
}
```

### New API

```json
{
  "启用": true,
  "名称": "示例 new-api",
  "类型": "newapi",
  "网址": "https://example-newapi.com",
  "账号": "your-username",
  "密码": "your-password",
  "余额提醒线": 1,
  "涨价禁用阈值": 0.5,
  "额度单位": 500000
}
```

## 涨价自动禁用

配置项：

```json
"涨价禁用阈值": 0.5
```

含义：

```text
涨幅 >= 50% 时，自动禁用该分组绑定的令牌。
```

例子：

```text
0.5 -> 0.75  触发
0.5 -> 1.0   触发
0.5 -> 0.6   不触发
```

如果不想自动禁用，可以留空：

```json
"涨价禁用阈值": ""
```

## 定时运行

推荐每 10 分钟检查一次：

```bash
*/10 * * * * cd /opt/sbaiapi && /usr/bin/python3 monitor.py >> /opt/sbaiapi/monitor.log 2>&1
```

`状态` 指令默认读取本地缓存结果，不会每次都实时请求上游站点。

## QQBot 常驻

systemd 示例：

```ini
[Unit]
Description=sbaiapi QQ query bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/sbaiapi
ExecStart=/usr/bin/python3 /opt/sbaiapi/qqbot_listener.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 运行状态文件

运行后会生成：

```text
state.json
qqbot_state.json
monitor.log
```

说明：

- `state.json` 保存最近一次检查结果和登录态。
- `qqbot_state.json` 保存 QQBot access_token 缓存。
- `monitor.log` 保存定时任务日志。

## 兼容 / 相关项目

sbaiapi 不是以下项目的官方插件，也没有官方关联，只是针对常见面板接口做了监控适配。

- sub2api：<https://github.com/Wei-Shaw/sub2api>
- New API：<https://github.com/QuantumNous/new-api>

## 安全提醒

不要提交真实运行文件：

- `config.json`
- `state.json`
- `qqbot_state.json`
- `monitor.log`

这些文件可能包含：

- 上游账号密码
- 登录态
- QQBot AppSecret
- QQBot access token

`.gitignore` 已经默认忽略这些文件。

## 联系方式

- QQ：`2867705759`
- API 服务：<https://yh.968968968.xyz/>

## License

MIT
