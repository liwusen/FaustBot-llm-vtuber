# mc-operator

这是 FaustBot 的 Minecraft 操作执行器，基于 `mineflayer` 实现。

## 作用

- 连接 Minecraft 服务器
- 接收 Faust 后端通过 WebSocket 发来的命令
- 调用 `mineflayer` 执行动作
- 把 Minecraft 事件再推送回 Faust，用于触发 Agent

## 协议

WebSocket 默认监听：`ws://127.0.0.1:18901`

### 命令请求

```json
{
    "type": "command",
    "request_id": "uuid",
    "name": "look-at-player",
    "args": {
        "player_name": "Steve"
    }
}
```

### 命令响应

```json
{
    "type": "command_result",
    "request_id": "uuid",
    "ok": true,
    "name": "look-at-player",
    "data": {
        "looked_at": "Steve"
    }
}
```

### 事件推送

```json
{
    "type": "event",
    "event_name": "hurted",
    "payload": {
        "health": 16,
        "position": [1, 64, 2]
    }
}
```

## 当前支持命令

- `connect-server`
- `disconnect-server`
- `get-status`
- `stop-current-action`
- `look-at-player`
- `look-at-position`
- `go-to-position`
- `follow-player`
- `get-mobs-around`
- `get-players-around`
- `eat-food`
- `chat`
- `equip-item`
- `hold-item`
- `interact-entity`
- `attack-entity`
- `inventory-summary`
- `nearby-blocks`
- `mine-block`
- `dig-block`
- `place-block`
- `collect-item-drop`

- `craft-item`
- `smelt-item`
- `open-chest`
- `withdraw-item`
- `deposit-item`
- `use-bed`
- `toss-item`

## 启动

安装依赖后运行：

```bash
npm start
```
