# 后端服务

Flask 后端是库存、家庭画像、菜谱、设备状态和电子纸渲染的权威服务。它通过 AI 的
Chat Completions 兼容接口完成视觉识别、文本生成和 ASR，并通过 HTTPS manifest 向设备发布菜单。

## 职责

- 食材识别、条码查询、单位规范化、库存和保质期管理。
- 家庭成员画像、库存硬约束、餐次适配、历史去重和菜谱生成。
- 唯一的智能冰箱贴菜单排版、六色量化与 `.film` 编码。
- 每设备版式偏好、四张套图发布、manifest、下载、强刷和同步状态。
- 每日任务、设备云端快照、固件托管和请求级日志。

一次菜单发布固定包含 `today`、`breakfast`、`lunch`、`dinner` 四个槽位，共用一个版本号。
发布前始终读取设备当前版式；版式或参数参与幂等键。已有菜单时，`PUT /api/devices/{id}/prefs`
会保存新偏好并强制重渲染该设备的完整四张。

## 目录

```text
src/app.py              Flask 路由、鉴权和请求生命周期
src/database.py         SQLite schema、库存、画像和菜谱
src/agent.py            菜谱生成、历史避让和完成后校验
src/recognition.py      视觉识别
src/inventory_units.py  件数、重量和容量规范化
src/menu_render.py      唯一菜单版式实现
src/film_encoder.py     六色电子纸 `.film` 编码
src/device_display.py   版本、套图、manifest 和设备快照
src/scheduler.py        每日生成与发布任务
src/config.py           环境配置与启动期安全校验
tests/                  单元、契约和集成测试
scripts/                部署自检和联调脚本
```

接口详情见 [docs/API.md](docs/API.md)，跨端兼容规则见
[../docs/contracts/README.md](../docs/contracts/README.md)。

## 本地运行

建议使用 Python 3.13；已有 `.venv` 时直接复用。

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
.venv/bin/python scripts/setup_demo_data.py
.venv/bin/python -m src.app
```

默认监听 `127.0.0.1:5000`，关闭 debug。至少配置：

```dotenv
AI_API_KEY=replace-with-provider-key
AI_BASE_URL=https://api.example.com/v1
AI_MODEL_VISION=vision-model
AI_MODEL_TEXT=text-model
AI_MODEL_ASR=speech-model
SFM_API_TOKEN=<openssl rand -hex 32 的输出>
```

`AI_API_KEY` 和模型标识必须由运行环境注入，不能写进代码或提交 `.env`。语音模型需支持
`input_audio` 与 `asr_options` 请求字段。完整变量和约束以
[.env.example](.env.example) 为准。

## 关键配置

| 变量 | 用途 |
| --- | --- |
| `SFM_HOST` / `SFM_PORT` | 监听地址和端口；非回环地址必须配置 `SFM_API_TOKEN` |
| `SFM_DATABASE_PATH` | SQLite 路径 |
| `SFM_FILM_DIR` | 版本化 `.film` 目录，必须与数据库一起持久化 |
| `SFM_BASE_URL` | 可选下载基址；通常留空并从设备请求推导 |
| `SFM_ENABLE_SCHEDULER` | WSGI 模式启动调度器；必须配合单 worker |
| `SFM_AUTO_PUBLISH_DEVICES` | 自动发布设备白名单；空表示所有登记设备 |
| `SFM_FONT_PATH` | 中文渲染字体；缺失时拒绝渲染而不是生成方框菜单 |
| `TZ` | 用餐和每日任务的本地时区，部署默认 `Asia/Shanghai` |

每日更新时间和自动发布开关由 `app_settings` 保存，App 修改后数据库值优先于环境变量。
用餐时间决定菜单内容与固件切图；旧 `lead_minutes` 字段仅用于兼容。

## 鉴权

- `app_token`：Android 和运维业务接口使用 `Authorization: Bearer {SFM_API_TOKEN}`。
- `device_token`：每台设备独立，manifest、`.film` 和设备主动刷新使用。
- `/healthz` 是免鉴权的轻量存活探针；`/api/health` 及其它健康子路由包含诊断数据，均需
  `app_token` 鉴权。

不要混用两种 Token。登记设备 Token 后，还需通过 BLE `0x42` 把相同值写入设备。

## Docker 与 HTTPS

固件拒绝明文 manifest，真实设备联调必须使用 HTTPS。Compose 由 Caddy 终结 TLS，后端只在
内部网络监听 5000：

```bash
cd backend
cp .env.example .env
bash ../embedded/smart_fridge_magnet/certs/gen-ca.sh 192.0.2.10
docker compose up -d --build
curl --cacert ../embedded/smart_fridge_magnet/certs/sfm_ca.pem \
  -H "Authorization: Bearer $SFM_API_TOKEN" \
  https://192.0.2.10:8443/api/health
```

自签 CA 还必须按证书文档显式追加到本地固件构建；公开默认固件只信任公共 CA，不会自动
信任上面新生成的 CA。

SQLite、`.film` 和日志统一挂载到 `/data`。容器重建时只保留数据库却丢失 `.film` 会产生
“manifest 有版本、下载文件 404”的不一致状态。部署后运行：

```bash
SFM_URL=https://<host>:8443 SFM_API_TOKEN=<app_token> \
  bash scripts/verify_cloud_link.sh
```

证书生成与信任链见
[../embedded/smart_fridge_magnet/certs/README.md](../embedded/smart_fridge_magnet/certs/README.md)。

## 菜谱与库存规则

- 主料只能来自当前库存，基础调料除外；库存不足时减少菜品。
- 早餐偏快手和家庭早餐，午餐较完整，晚餐较清淡。
- 当天其它餐次和当前待替换方案为硬避让，最近 7 天为软避让。
- 非流式请求最多一次综合纠正；流式完成后过滤违规菜，全部无效时不落库。
- `quantity + unit` 表达件数或容量，`weight_grams` 表达重量；容量统一保存为 ml。
- 仅纯净水、矿泉水、饮用水可在识别只返回重量时窄范围按 `1g ~= 1ml` 纠正。

## 测试与质量

```bash
cd backend
.venv/bin/python -m pytest
.venv/bin/ruff check src
```

需要真实已启动服务的脚本默认跳过；显式执行时使用 pytest 的 integration 选项。测试必须把
SQLite 和 `.film` 指向临时目录，不能触碰开发或真机数据。版式输出发生变化时递增
`menu_render.RENDER_REVISION`；仅改变发布编排或接口语义时不递增像素渲染版本。

## 运维检查

- `GET /healthz`：轻量存活检查。
- `GET /api/health`：数据库、库存和调度器状态。
- `GET /api/devices/{id}/link-check`：定位 Token、版本、下载地址和设备联系状态。
- `GET /api/devices/{id}/sync-status`：原始版本、槽位、应用状态和云端快照。
- `POST /api/devices/{id}/display/force`：让设备下一次 manifest 强制重拉当前版本。

发布能力见根目录 CHANGELOG；硬件行为必须按固件 README 的真机验收清单复核。
