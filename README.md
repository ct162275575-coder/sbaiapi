# sbaiapi

轻量上游 API 监控工具。

主要用途：

- 监控 subapi / newapi 上游余额。
- 自动读取令牌绑定分组。
- 对比分组倍率是否涨价或降价。
- 涨价幅度达到阈值时自动禁用对应分组令牌。
- 通过官方 QQ 机器人推送异常提醒。
- QQ 发送 `状态` 查看最近一次监控缓存。

## 功能

- 支持 `subapi`：
  - 登录接口：`/api/v1/auth/login`
  - 余额接口：`/api/v1/auth/me`
  - 分组接口：`/api/v1/groups/available`
  - 密钥接口：`/api/v1/keys`
- 支持 `newapi`：
  - 登录接口：`/api/user/login`
  - 余额接口：`/api/user/self`
  - 价格接口：`/api/pricing`
  - 令牌接口：`/api/token`
- 支持 QQBot 单聊卡片消息。
- 支持定时检查。

## 安装

```bash
pip install -r requirements.txt
```

## 配置

复制示例配置：

```bash
cp config.example.json config.json
```

然后编辑：

```text
config.json
```

注意：

- `config.json` 包含账号、密码、QQBot 密钥，不要提交到 Git。
- `.gitignore` 已经默认忽略 `config.json`、`state.json`、`qqbot_state.json`。

## 运行一次检查

```bash
python monitor.py
```

Windows 可以双击：

```text
run_once.bat
```

## 启动 QQ 查询机器人

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

机器人会返回站点卡片。

## 定时任务示例

Linux crontab：

```bash
*/10 * * * * cd /opt/sbaiapi && /usr/bin/python3 monitor.py >> /opt/sbaiapi/monitor.log 2>&1
```

## QQBot 常驻示例

systemd：

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

## 涨价自动禁用

配置项：

```json
"涨价禁用阈值": 0.5
```

含义：

```text
涨幅 >= 50% 时，自动禁用该分组绑定的令牌。
```

比如：

```text
0.5 -> 0.75  触发
0.5 -> 1.0   触发
0.5 -> 0.6   不触发
```

## 运行状态文件

运行后会生成：

```text
state.json
qqbot_state.json
```

说明：

- `state.json` 保存最近一次检查结果和登录态。
- `qqbot_state.json` 保存 QQBot access_token 缓存。

这两个文件不要提交到 Git。
