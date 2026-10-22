# QQ Bot 搭建指南（使用 Docker Compose）

## 架构

```
你的 QQ 
  ↓
NapCatQQ (QQ 无头框架，用小号登录)
  ↓
AstrBot (AI 机器人框架)
  ↓  
本地代理 proxy:8766 (OpenAI 兼容)
  ↓
Hermes → GitHub Copilot（`~/.hermes/.env` 认证）
```

## 前置要求

- Docker & Docker Compose（已装有）
- 一个 QQ 小号（用来跑机器人）
- Mac 上的 Docker Desktop（需要网络共享支持）

## 步骤

### 1. 检查文件结构

```bash
cd ~/qqbot
ls -la
```

应该看到：
```
├── docker-compose.yml       # Docker 配置
├── proxy/                   # 本地 Mock API
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── napcat/                  # NapCatQQ 配置目录
│   ├── config/
│   └── QQ/
├── astrbot/                 # AstrBot 数据目录
│   └── data/
└── astrbot_config.toml      # AstrBot 配置
```

### 2. 构建本地 API 容器

```bash
cd ~/qqbot/proxy
docker build -t copilot-proxy:latest .
```

### 3. 启动所有服务

```bash
cd ~/qqbot
docker-compose up -d
```

检查状态：
```bash
docker-compose ps
```

应该看到三个容器都在 Running 状态。

### 4. 查看日志

```bash
# 查看本地 API 日志
docker logs -f copilot-proxy

# 查看 NapCatQQ 日志
docker logs -f napcat

# 查看 AstrBot 日志
docker logs -f astrbot
```

### 5. NapCatQQ 配置（QQ 小号登录）

第一次运行时，NapCatQQ 会在 napcat/config 下生成配置文件。

打开浏览器访问：http://127.0.0.1:3000

按照提示用你的 QQ 小号扫码登录。

### 6. AstrBot WebUI

打开浏览器访问：http://127.0.0.1:6185

配置 AstrBot，主要是：
- 连接到 NapCatQQ
- LLM 后端指向本地 Mock API（`http://127.0.0.1:8765`）

### 7. 测试

在 QQ 上私聊机器人号，或把它拉进一个群，试试问问题。

机器人应该会用 Mock 回复（包含你输入的内容）。

## 常用命令

```bash
# 启动
docker-compose up -d

# 停止
docker-compose down

# 查看日志（跟踪）
docker-compose logs -f [服务名]

# 重建容器
docker-compose up -d --build

# 进入容器 shell
docker exec -it [容器名] /bin/bash
```

## 启动本地代理（Hermes + Copilot）

本机需已安装 Hermes（`~/.hermes/hermes-agent`），Copilot 令牌配置在 `~/.hermes/.env`（如 `COPILOT_GITHUB_TOKEN`）。

```bash
~/qqbot/start_proxy.sh
# 停止并禁用保活：~/qqbot/stop_proxy.sh
```

可选环境变量：`QQBOT_MODEL`、`QQBOT_MODEL_HEAVY`（在启动代理的 shell 或 cron 里 export）。

## 故障排查

**Q: 容器启动失败？**
A: 检查 Docker 是否运行，检查端口是否被占用
```bash
lsof -i :8765  # 检查 8765 端口
docker ps -a   # 查看所有容器（含停止的）
```

**Q: NapCatQQ 登录失败？**
A: 确保用的是真实的 QQ 小号（不是小程序号），检查 WiFi 连接

**Q: AstrBot 无法连接到本地 API？**
A: 检查 Docker 网络配置，确保容器间通信正常
```bash
docker network ls
docker inspect qqbot-network
```

**Q: 想看详细日志？**
A: 修改 docker-compose.yml 中的环境变量
```yaml
environment:
  - DEBUG=true
  - LOG_LEVEL=debug
```

## 弟弟口头指令（Oneesan 插件）

只有 QQ **273585129**（弟弟）可以下指令，私聊和群聊都行。用自然语言说「要做什么」，模型会映射到固定工具；纯聊天、提问、吐槽不会当指令执行。

群聊关掉时，弟弟的指令仍然有效；普通群友无法通过转述、引用或提示词触发工具。

### 快捷开关（不用等模型解析）

| 意图 | 说法示例 |
|------|----------|
| 开群聊 | 打开群聊、群聊开启、恢复群聊 |
| 关群聊 | 关闭群聊、关掉群聊、群聊关了 |
| 问群聊 | 群聊状态、群聊开着吗 |
| 开语音 | 打开语音、语音开启 |
| 关语音 | 关闭语音、关掉语音 |
| 问语音 | 语音状态、语音关着吗 |

### 低风险（一般直接执行）

| 能力 | 怎么说（示例） |
|------|----------------|
| 总状态 | 现在什么状态、群聊和语音怎样 |
| 开关群聊 / 语音 | 把群聊关掉、把语音打开（也可走快捷句） |
| 心情 | 你现在什么心情 |
| 查记忆 | 你还记得大肥鱼什么、查一下弟弟最近的事 |
| 网页搜索 | 帮我搜一下 qqbot 教程 |
| 现在 B 站学习 | 现在去 B 站学一下 Docker（主题可空，空则自己选题） |
| 现在萌娘百科学人格 | 现在从萌娘百科再学一批姐姐（可加主题） |
| 学说法 | 去学一下说话方式 |
| 整理记忆 | 把记忆整理一下 |
| 私聊某人 | 私聊大肥鱼问一下他到家没（QQ 或记忆里**唯一**昵称） |
| 群里发一条 | 去群里说我晚点来、在这个群说大家好 |
| 当前会话回复 | 在这里回一句「知道了」（群里下令则确认后回群里） |
| 健康检查 | 程序还正常吗、检查一下 |
| 操作记录 | 看最近操作日志（可说要多少条） |
| 本机任务进度 | 上一个电脑任务怎么样了 |
| 发语音 | 把「晚安」发成语音 |
| 主动私聊 | 现在主动给我发一条 |

**发消息注意：** 一次只发一个目标；称呼对不上或重名会追问，不猜。发给**陌生 QQ 号**（记忆里没这个人）要先私聊确认。

### 高风险（必须私聊确认）

| 能力 | 怎么说（示例） |
|------|----------------|
| 操作电脑 | 打开浏览器完成某某、在这台电脑上…… |
| 改项目文件 | 检查程序并修好、改一下插件里的某某 |
| 重启服务 | 重启机器人和代理 |

**确认规则：**

- 群里下高风险指令时，群里只回：「需要去私聊确认，群里不会说具体内容。」摘要和四位码**只发私聊**。
- 在私聊回复 `确认 XXXX` 执行；`取消` 作废。码 **10 分钟**有效，用过不能重复。
- 涉及删除、清空、覆盖时要**再确认一次**（仍在私聊）。
- 本机执行需要 Cursor API key；未配置会回复「执行器未配置」，不会改成直接跑 shell。

### 说法示例对照

| 你大概的意思 | 实际走的工具 |
|--------------|--------------|
| 去 791213409 群说我晚点来 | send_group |
| 私聊大肥鱼问进度 | send_private |
| 现在从萌娘百科再学一批 | learn_moegirl |
| 给我找个教程（只要链接） | search（不是改代码） |
| 检查程序并修好 | edit_project → 确认 → 本机 SDK |
| 打开浏览器完成某事 | computer → 确认 → 本机 SDK |

普通对话（撒娇、问天气、闲聊）不会进工具表，仍走原来的聊天和兴趣接话逻辑。

## 下一步

1. 跑通整个框架
2. 验证 QQ <-> Bot 通信正常
3. 测试各种 prompt（确保 Mock API 能处理）
4. 集成真实的推理引擎
5. 添加更多 AstrBot 插件

祝好运～
