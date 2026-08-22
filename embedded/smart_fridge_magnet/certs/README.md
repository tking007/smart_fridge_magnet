# 固件 TLS 信任锚

设备通过 HTTPS 拉取 manifest、`.film` 和固件。默认固件只使用 ESP-IDF `esp_crt_bundle`
中的公共 CA，不携带本项目生成的信任锚。局域网自签部署时，必须在本机生成 CA，并在本地
构建配置中显式追加它。

## 生成证书

```bash
cd embedded/smart_fridge_magnet/certs
bash gen-ca.sh 192.0.2.10
bash gen-ca.sh 192.0.2.20 sfm.example.com
```

所有设备会访问的 IP 和域名都必须写入服务器证书 SAN。IP 使用 `IP:` SAN，域名使用
`DNS:` SAN；两者不可互换。

| 产物 | 位置 | 是否入库 |
| --- | --- | --- |
| `sfm_ca.pem` | 本目录，按需编入本地固件 | 否，本机生成物 |
| `sfm_ca.key` | 本目录 | 否 |
| `sfm_server.pem` | `backend/certs/` | 否，本机生成物 |
| `sfm_server.key` | `backend/certs/` | 否 |

私钥不得进入 Git、日志或聊天工具。泄露 CA 私钥后必须更换 CA 并重新烧录信任该 CA 的设备。

## 部署检查

- 设备所在网络必须能路由到后端地址。
- 设备需要先通过 SNTP 获得可信时间，再验证服务器证书有效期。
- 服务器换 IP/域名时重签服务器证书；CA 不变则无需重烧固件。
- CA 变化才需要重新构建和烧录本地自签配置的固件。
- 生成自签 CA 后，在 `idf.py menuconfig` 中启用自定义证书 bundle，并将路径设为
  `certs/sfm_ca.pem`；该设置只保留在本地 `sdkconfig`，不要提交证书或本地配置。

验证时使用 CA，不要用 `curl -k`：

```bash
curl --cacert embedded/smart_fridge_magnet/certs/sfm_ca.pem \
  -H "Authorization: Bearer $SFM_API_TOKEN" \
  https://<host>:8443/api/health
```
