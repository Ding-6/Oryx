# Oryx 中继服务器

PC 和手机通过它配对。它只转发密文，不知道屏幕内容，也不能伪造操作。

```
[Oryx 桌面] --wss--> [中继] <--wss-- [iOS App]
```

## 为什么要中转

家用宽带通常没有公网 IP，端口映射也有风险。让两端都主动向外连中继，
既不需要公网 IP，也不需要在家里的路由器上开洞。

## 部署

### 最快：Docker + Caddy 一条命令

仓库里已经带好了 `Dockerfile`、`docker-compose.yml`、`Caddyfile`。

```bash
# 1. 服务器上拉代码
git clone <你的仓库> oryx && cd oryx/relay

# 2. 域名解析到这台机器，然后跑
ORYX_DOMAIN=relay.你的域名.com docker compose up -d

# 3. 验证
curl https://relay.你的域名.com/health
# {"rooms":0,"messages":0}
```

Caddy 会自动申请续期 Let's Encrypt 证书，80/443 需要放行。

然后把地址填进电脑端：

```json
// %APPDATA%\Oryx\config.json
{ "remote_relay": "wss://relay.你的域名.com" }
```

### 方式一：直接跑（调试用）

```bash
python relay/server.py --host 0.0.0.0 --port 8899
```

依赖只有一个：`pip install websockets`。

### 方式二：云平台

Render / Fly.io / Railway 都支持直接跑这个脚本，注意两点：

1. 平台通常用 `$PORT` 环境变量指定端口，脚本已支持。
2. 平台一般自动给 `https/wss`，把公网域名填到 `config.json` 的 `remote_relay`。

## 必须配 TLS

生产环境请在前面套 Caddy 或 Nginx，让手机连 `wss://`。

仓库自带的 `Caddyfile` 已经是可用配置：

```caddy
{$ORYX_DOMAIN:relay.example.com} {
	encode zstd gzip
	reverse_proxy relay:8899 {
		transport http {
			read_timeout 300s
			write_timeout 300s
		}
	}
}
```

读超时放宽到 300 秒，是因为画面帧持续推送，默认超时会把长连接掐断。

手机端 App 里填 `wss://relay.example.com` 即可。

## 探活

```bash
curl https://relay.example.com/health
# {"rooms":0,"messages":0}
```

## 协议

WebSocket，路径 `/ws`，两个查询参数：

| 参数 | 值 |
|---|---|
| `role` | `host`（电脑）或 `client`（手机） |
| `room` | 配对码，双方必须一致 |

服务端行为：

- 每个房间最多一个 host + 一个 client，重复连接会被拒绝（关闭码 1008）。
- 一端发来的二进制消息**原样转发**给另一端；对端未连接时直接丢弃，不排队。
- 单条消息上限 4MB；ping 20 秒、超时 40 秒。

## 安全边界（务必读）

| 项 | 说明 |
|---|---|
| 内容加密 | 端到端 AES-GCM，中继只看到密文 |
| 房间号 | 就是配对码，**中继能看到** |
| 爆破风险 | 配对码 6–8 位，中继若被攻破可离线暴力破解。需要更强保证就用更长配对码或改造成 PAKE |
| 权限 | 一旦配对成功，手机可以完全控制鼠标键盘 |
| 建议 | 自建中继；用完就关；不要在公共中继上传敏感画面 |

## 本地测试

```bash
# 终端 1
python relay/server.py --host 127.0.0.1 --port 8899

# 终端 2（PC 端内核，配置里把 remote_relay 设为 ws://127.0.0.1:8899）
cd core && python -m oryx
```

界面上点「远程」即生成配对码。
