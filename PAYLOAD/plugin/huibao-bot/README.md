# 汇报助手 huibao-bot

把小歪（龙数小外桌面机器人）变成工作汇报终端：**服务器侧聚合今日议程 → 生成「重点一句 + 清单」→ 推给机器人念出来**；
机器人平时照旧跟人语音问答，问「今天要干什么」时按实时数据回答。

---

## 1. 部署位置

| 组件 | 路径 / 地址 |
| --- | --- |
| 小歪语音服务（deskbot-server + LiveKit + LampGo worker） | `/home/lgsj/01-DSH/11-汇报机器人/longshu-xiaowai-v2-upstream-full-r6/service` |
| 服务控制脚本 | `/home/lgsj/01-DSH/11-汇报机器人/服务控制.sh`（`start/stop/restart/status/logs`） |
| DSH 插件源码 | `/home/lgsj/01-DSH/12-汇报助手/huibao-bot` |
| 插件安装位置 | `~/.dsh/profiles/web/node_modules/@local/huibao-bot`（pnpm link） |
| 插件运行数据 | `~/.dsh/huibao-bot/{config.json,state.json}` |
| DSH web 工作台 | `http://127.0.0.1:3080`（对外 `dsh.ilica.cn`） |
| host 启动命令备份 | `/home/lgsj/01-DSH/12-汇报助手/host.cmdline.txt` |

### 端口

| 端口 | 用途 |
| --- | --- |
| 9000 | deskbot-server：设备 WebSocket（pb 协议）+ `/api/*`（仅 loopback） |
| 9020 | `wifi_link`：机器人 WiFi TCP 接入 |
| 9021 | core_discovery：局域网 UDP 发现 |
| 7880 / 7881 | 本机 LiveKit（RTC 语音房间） |
| 18790 | RTC token 端点 |
| 3080 | DSH 工作台 |

---

## 2. 语音链路如何工作

```
机器人(ESP32-S3) ──USB CDC──┐
                └──WiFi TCP(9020)──► deskbot-server(9000)
                                          │  ASR: Fun-ASR（经 modelhub 网关）
                                          │  LLM: deepseek-v4.1-flash
                                          │  TTS: Qwen3-TTS（WAV 24k → opus）
                                          ▼
                                   下行到设备扬声器
LiveKit(7880) + LampGo worker(agent) ──► 全双工 / barge-in（R6 esp32_aec 模式）
```

插件只做**编排**：聚合事实 → 生成播报稿 → `POST /api/device_tts`。语音合成与设备下行都由 deskbot-server 负责。

---

## 3. 插件 API（`/api/huibao-bot/*`，同一工作台认证门禁）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 插件 + 小歪服务 + 设备列表 |
| GET | `/status` | 上面 + 各项统计、数据源健康、脱敏配置、最近提醒记录 |
| GET | `/digest?mode=full\|brief` | 今日汇报稿（headline / speakText / sections / counts） |
| POST | `/speak` | `{text?, deviceId?}`：给 text 就念这段，不给就生成今日汇报再念 |
| GET/POST/DELETE | `/chat` | 小歪 Bot 对话历史 / 提问 `{text, speak?}` / 清空 |
| GET/PUT | `/config` | 配置读取 / 局部更新（`apiKey` 打码值不会回写） |
| GET/POST | `/todos` | 手动待办列表 / 新增 `{text, due?, priority?}` |
| PATCH/DELETE | `/todos/<id>` | 勾选完成 / 删除 |
| POST | `/reminder/test` | 立即跑一次提醒（`dryRun:true` 只出稿不播） |

### 注册给 Agent 的工具

`huibao_daily_digest`、`huibao_today`、`huibao_speak`、`huibao_add_todo`、`huibao_device_status`

---

## 4. 数据源

| 源 | 位置 | 现状 |
| --- | --- | --- |
| 会议日程 | `~/.dsh/meeting-assistant/meetings.json` | **目前不存在**（会议助手尚未同步过会议），插件已容错并在面板提示 |
| Bot 群聊 | `~/.dsh/bot/state.json` | 结构已适配，当前 `rooms/exchanges` 为空 |
| 工作台任务 | `~/.dsh/workbench-bot/state.json` | 结构已适配，当前无运行中的 run |
| 系统告警 | `systemctl --failed` | **可用**（已过滤 exitnode/egress/hardening 等预期单元） |
| 手动待办 | `~/.dsh/huibao-bot/state.json` | **可用**，面板「待办」页维护 |
| 项目/供应链进度 | `config.projects` + `sources.projects` | **扩展位**，默认关闭 |

> 逾期结转、未来三天预告已实现，随会议/待办数据自动出现。

---

## 5. 行为策略

- **被动为主**：面板「问小歪」或机器人语音问答，按「重点一句 + 清单」回答；
- **主动只在会前**：每 30 秒检查一次，命中「开会前 N 分钟」才播报（默认 10 分钟）；
- 静默时段默认 `22:00–08:00`，每小时最多 4 次，去重键 = 会议 id + 开始时间；
- 模型润色失败自动回退到确定性模板稿，**任何情况下都有一句话能念**。

---

## 6. 验收步骤（插线那一刻就能跑）

```bash
# 0) 服务状态
bash ~/01-DSH/11-汇报机器人/服务控制.sh status
#   期望三项全「运行中」，health: {"ok": true}

# 1) 插 USB 后确认设备被识别（devices 里应出现非空 device_id）
bash ~/01-DSH/11-汇报机器人/服务控制.sh status

# 2) 工作台里：左侧「汇报助手」→ 今日汇报 → 点「念给我听」
#    机器人应念出今天的重点与清单

# 3) 会前提醒：设置页把提前分钟改成 2，造一个 3 分钟后的会议后再看
#    （或直接点 POST /api/huibao-bot/reminder/test 立刻验证播报）

# 4) 语音问答：对机器人说「今天要干什么」，应回答重点 + 清单
```

> `/api/devices` 必须带正确的 `x-api-key`。密钥在 `service/data/.free_api_key`，但那是 **INI 格式**：
> 要取 `api_key=` 后面的值（`grep -oP '(?<=api_key=).*'`），不能整文件当密钥——含中文会触发服务端 500。


---

## 7. 已知限制与后续

1. **会议数据为空**：需要会议助手（dsh-meetai）先同步一次会议，之后日程自动进汇报；
2. **项目/供应链进度是扩展位**：接产品库（8899）或表格时，只需在 `sources.js` 加一个 provider 并在 `config.projects` 打开开关；
3. **pnpm 必须是 12.x（JS 版）**：本机 `/usr/local/bin/pnpm`（compiled binary）对 npm 镜像的 TLS 握手会被重置，
   安装插件请用 `node /home/lgsj/pnpm-new/node_modules/pnpm/bin/pnpm.mjs`；
4. **ffmpeg / 串口权限**：已加 `/etc/udev/rules.d/99-deskbot.rules`（ESP32-S3 VID `303a` 权限 0666），
   并把 `lgsj` 加入 `dialout` 组（**需重新登录会话生效**）；`ffmpeg` 未装上，但**不在关键路径**
   （云端 TTS 直接返回 WAV，设备侧 opus 由 opuslib 编码）；
5. **开机自启**：已加用户 crontab `@reboot sleep 25 && … 服务控制.sh start`，日志写 `~/01-DSH/11-汇报机器人/boot.log`。
   **DSH host 本身仍为手动启动**（用 `host.cmdline.txt` 里的命令 + `setsid`），重启后需自己拉起；
6. **`status` 里 agent 检测坑**：lampgo worker 的进程命令行是 `deskbot_server.rtc_agent_entry`，
   不含 `lampgo` 字样，`pgrep` 模式必须包含 `rtc_agent_entry` 才不会误报「未运行」。


---

## 8. 回滚

```bash
# 插件回滚（移除 bundle + 依赖）
cd ~/.dsh/profiles/web
cp package.json.bak-huibao2-* package.json       # 选加插件之前的备份
node /home/lgsj/pnpm-new/node_modules/pnpm/bin/pnpm.mjs install --no-frozen-lockfile
# 然后按 host.cmdline.txt 重启 host
```
