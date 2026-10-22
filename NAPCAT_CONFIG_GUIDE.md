# NapCat WebSocket 反向连接配置指南

## 访问 NapCat WebUI

使用 Token 访问：
```
http://127.0.0.1:6099/webui?token=a04679734328
```

## 配置步骤

### 1. 进入网络配置
- 点击左侧菜单 **"网络配置"**

### 2. 查找 WebSocket 反向连接（或相似项）

在网络配置列表中，找到或新建 **"WebSocket 客户端"** 或 **"WebSocket 反向连接"** 的配置项。

### 3. 填写以下参数

| 字段 | 值 |
|------|-----|
| **启用 / Enable** | ✅ 勾选 |
| **Host / 主机** | `astrbot` 或 `127.0.0.1` |
| **Port / 端口** | `6199` |
| **URL（如果有）** | `ws://astrbot:6199/ws` |
| **Token / 令牌** | 留空（或根据需要填写） |

### 4. 点击"保存"或"连接"

配置保存后，NapCat 应该会自动尝试连接到 AstrBot 的反向 WebSocket 服务。

## 验证连接

连接成功后，可以在以下地方看到确认：

### 检查 AstrBot 日志
```bash
docker logs astrbot | grep -i "connected\|已连接"
```

应该看到类似的消息：
```
aiocqhttp(OneBot v11) 适配器已连接
```

### 检查 NapCat 日志
```bash
docker logs napcat | grep -i "websocket"
```

## 如果还是连接不上

1. **确保 Host 正确**
   - Docker 网络中用容器名：`astrbot`
   - 或用具体 IP（运行下面命令查看）：
   ```bash
   docker inspect astrbot | grep IPAddress
   ```

2. **检查 AstrBot 是否在监听**
   ```bash
   docker exec astrbot netstat -tuln | grep 6199
   ```
   应该看到 `LISTEN` 状态

3. **重启容器**
   ```bash
   docker-compose restart napcat astrbot
   ```

