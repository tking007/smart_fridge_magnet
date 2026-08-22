# 跨端契约

本文记录 Android、后端和 ESP32-S3 之间必须同时保持一致的公开契约。当前公开快照版本为
**v0.19.3**。真机耗时、低功耗行为和多尺寸 UI 仍需按固件验证清单复核。

详细 HTTP 请求与响应见 [后端 API](../../backend/docs/API.md)，BLE 逐字节协议见
[固件 BLE 协议](../../embedded/smart_fridge_magnet/docs/ble_protocol.md)，版本变化见
[CHANGELOG](../../CHANGELOG.md)。本文不重复端内实现过程和历史问题。

## 责任边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| Android | 食材确认、库存与家庭设置、菜谱、预览、BLE 配置和设备操作 | 菜单排版、`.film` 编码 |
| 后端 | 识别、库存、画像、菜谱、唯一菜单渲染、版本发布、manifest、固件托管 | Android UI、电子纸驱动 |
| ESP32-S3 | BLE、HTTPS 下载、文件校验、原子提交、按时段选图、电子纸刷新和深睡 | 菜谱 JSON、中文排版、模型调用 |

跨端不变量：

- 菜单渲染和 `.film` 编码只有后端一份实现。
- App 预览解码后端产物，设备显示同一份字节，保证预览与真机一致。
- ESP32 只识别文件、版本、槽位和文件名，不理解菜谱内容。
- 外部模型 Key、App Token、设备 Token 和 Wi-Fi 密码不得互相复用或写入仓库。

## 库存与菜谱

### 分类

分类值固定为：

`蔬菜`、`水果`、`肉类`、`海鲜`、`蛋类`、`乳制品`、`豆制品`、`主食`、
`调味品`、`饮料`、`零食`、`冷冻食品`、`其他`。

后端以 `database.CATEGORY_DEFINITIONS` 为准，Android 通过
`IngredientCategory.backendName` 映射。饮料和零食默认不进入烹饪食材摘要。

### 数量与保质期

| 语义 | HTTP / DB | 约束 |
| --- | --- | --- |
| 件数或包装数 | `quantity + unit` | `unit` 可为个、瓶、盒、袋、包等 |
| 容量 | `quantity + unit=ml` | `L/l/升/毫升` 入站统一换算为 ml |
| 净重 | `weight_grams` | 单位固定为 g，可与包装数同时存在；`kg/千克/公斤` 换算后与入站克重取较大值 |
| 识别可信度 | `confidence` | 可空，仅允许高、中、低 |
| 保质期 | `shelf_life_days` | `1..3650` 天 |
| 日期 | `production_date / expiry_date` | `YYYY-MM-DD` |

件数、容量和净重至少一项大于 0。只有纯净水、矿泉水、饮用水在仅识别出克重时允许按
`1g≈1ml` 窄范围纠正，并把可信度降为中；其它饮料不推测密度。

菜谱主料必须来自现有库存，基础调料除外。早餐、午餐、晚餐使用各自餐次画像。硬避让
（必须换掉）只取每个餐次最近一条方案：本餐次不限日期，其它餐次限当天；近 7 天其余方案
为软避让，只在 prompt 里提示、不参与剔除。A、B 两套之间允许共用菜品，去重只在单套内做。
库存不足时减少菜品或返回明确原因，不补库存外主料。

## 六色电子纸格式

| 项目 | 固定值 |
| --- | --- |
| 画布 | `792 x 528` |
| 文件头 | 32 字节 |
| 像素 | 每像素 4 bit，偶数像素在高半字节 |
| 像素区 | 209088 字节 |
| 文件总长 | `209120` 字节 |
| 调色板索引 | `0..5` |
| 文件头设备色值 | `00 / FF / FC / E0 / 03 / 1C` |

文件头保存真实设备色值，像素半字节保存调色板索引。后端发布前和固件落盘前都必须校验
尺寸、总长、调色板索引和 SHA-256；失败时保留上一有效版本和原屏幕。

`.film` 规格、文件命名或像素渲染发生变化时必须同步三端，并递增
`backend/src/menu_render.py` 的 `RENDER_REVISION`。仅改变接口编排或预览方向不递增。

## 四菜单版本

正常菜单发布一次产生一个版本号和四个槽位：

| 槽位 | 文件名 | 用途 |
| --- | --- | --- |
| `today` | `1_today.film` | 今日总览 |
| `breakfast` | `2_breakfast.film` | 早餐详情，06:00-09:00 |
| `lunch` | `3_lunch.film` | 午餐详情，11:00-13:30 |
| `dinner` | `4_dinner.film` | 晚餐详情，17:00-19:30 |

文件名前缀决定按键轮播顺序，英文餐次关键字决定自动选图。自定义图片使用
`photo_*.film` 或 `draw_*.film`，不得包含上述四个餐次关键字。

后端先校验完整四张，再以单一版本提交。固件下载时任一张失败都不得推进
`last_applied_version`；全部成功后才清理旧菜单、选择当前时段画面并发送 applied ACK。

单张兼容发布使用 `current` 槽位，不带 `items[]`。它只更新当前图，不能替代需要全局
一致的四菜单发布。

## 版式

版式键固定为 `columns`、`columns_big`、`list`、`list_big`、`portrait`。
App 从 `GET /api/render/layouts` 获取选项，不另写一份展示枚举。

`layout_options` 当前字段：

| 字段 | 值域 | 默认 |
| --- | --- | --- |
| `accent` | `meal / red / green / blue` | `meal` |
| `font_size` | `normal / large` | `normal` |
| `density` | `comfortable / compact` | `comfortable` |
| `separator` | `block / rail / none` | `block` |

`PUT /api/devices/{id}/prefs` 的行为：

- 偏好未变化时不创建新版本，返回 `display_updated=false`。
- 设备从未发布菜单时只保存偏好，首次发布直接使用它。
- 已有菜单且偏好变化时，仅为目标设备重新渲染并发布完整四张。
- 可选 `republish=true`：偏好未变化也照发一次，供人工重试。
- 发布成功后返回 `display_updated=true`。
- 发布失败时恢复原偏好，返回 500、`preferences_saved=false` 和具体错误；旧套图与
  applied version 保持不变，相同请求可直接重试。`republish=true` 触发的失败不涉及回滚，
  不返回 `preferences_saved`。

版式和全部版式参数进入发布幂等键。发布前读取当前偏好，避免偏好变化后复用旧版式产物。

手动渲染同样使用设备偏好：`POST /api/devices/{id}/display/render` 与
`POST /api/devices/{id}/display/render-from-plan` 从路径中的设备编号读取版式；
`POST /api/render/film` 可在 JSON 中携带可选 `device_id`，供 BLE 菜谱直传使用。不带
`device_id` 时继续按默认版式渲染，兼容旧客户端与不绑定设备的工具。

`POST /api/scheduler/trigger/publish` 接受可选非空 `device_id`；App 的人工下发必须携带当前绑定
设备编号，只发布该设备的四张菜单。省略时保留面向全部配置目标设备的旧行为，供调度与运维使用。

## Manifest 与鉴权

Android 业务接口使用：

```http
Authorization: Bearer {app_token}
```

ESP32 的 manifest、`.film` 和固件下载使用：

```http
Authorization: Bearer {device_token}
```

`device_token` 必须同时通过 BLE `0x42` 写入设备，并通过
`POST /api/devices/{id}/token` 登记到后端。未登记、为空或不匹配一律返回 401。
`/healthz` 仅用于轻量存活探测，可豁免鉴权；`/api/health` 返回诊断摘要，必须使用 `app_token`。

整套发布的 manifest 兼容结构：

```json
{
  "version": 17,
  "film_url": "https://host/api/devices/demo/display/17/1_today.film",
  "size": 209120,
  "sha256": "64-char-lowercase-hex",
  "force": false,
  "items": [
    {"slot": "today", "name": "1_today.film", "url": "https://host/...", "size": 209120, "sha256": "..."},
    {"slot": "breakfast", "name": "2_breakfast.film", "url": "https://host/...", "size": 209120, "sha256": "..."},
    {"slot": "lunch", "name": "3_lunch.film", "url": "https://host/...", "size": 209120, "sha256": "..."},
    {"slot": "dinner", "name": "4_dinner.film", "url": "https://host/...", "size": 209120, "sha256": "..."}
  ],
  "firmware": {
    "version": "0.19.3",
    "url": "https://host/api/firmware/sfm-0.19.3.bin",
    "size": 1700000,
    "sha256": "..."
  }
}
```

兼容规则：

- 顶层 `film_url / size / sha256` 始终保留并指向总览，旧固件可继续下载单张。
- `items[]` 最多四条；任一条缺字段、文件名不安全、长度或摘要非法时拒绝整份 manifest。
- `items[]` 缺失或为空时走单张兼容路径。
- `firmware` 可选；任一字段非法时不执行 OTA，不影响当前有效画面。
- manifest、文件和固件 URL 必须使用设备可访问的 HTTPS 地址。

## 云端状态快照

设备拉取 manifest 时顺带上报非机密状态：

| 请求头 | 值 |
| --- | --- |
| `X-Device-Battery` | `0..100` |
| `X-Device-Firmware` | 固件版本 |
| `X-Device-Applied-Version` | 已成功上屏版本 |
| `X-Device-Sleep-Mode` | `0/1` |
| `X-Device-Wake-Slots` | 逗号分隔分钟值，如 `425,695,1085`；无槽位为 `none` |
| `X-Device-Wake-Fallback-Minutes` | 兜底唤醒分钟数 |
| `X-Device-Poll-Interval-Seconds` | 轮询周期秒数，默认 `86400` |

每个字段独立校验。缺失或非法字段不影响 manifest，也不覆盖上次合法值。请求头不得包含
SSID、Wi-Fi 密码或 Token。

`GET /api/devices/{id}/sync-status` 返回最近云端快照、`status_age_minutes`、最新发布版本、
完整槽位和事件。App 始终标注上报时间，超过 36 小时显示“可能已过期”；Wi-Fi 当前连接、
busy、最近错误、文件列表和配置回读仅在 BLE 连接后作为实时状态展示。

预览接口 `GET /api/devices/{id}/display/preview.png` 默认保持 `792 x 528`；显式传
`orientation=portrait` 时顺时针转正为 `528 x 792` 后再缩放。

## 低功耗与按键

默认唤醒槽位为 `425,695,1085`，即 07:05、11:35、18:05，兜底为 `1440` 分钟。
App 根据家庭餐次与提前量整表下发，不复制固件默认时间作为业务配置。

每天第一个尚未成功同步的计划唤醒会联网下载四张；成功后当天后续餐次只切换本地文件。
同步失败依次安排 15、60、240 分钟重试，第 4 次后停止额外重试。主动 FN 唤醒、BLE
`SYNC_NOW` 和醒着状态的长按命令不受“每天自动成功联网一次”限制。

低功耗模式的 Wi-Fi 只在同步事务期间开启，最后一个网络任务结束后立即关闭；设备不会在
BLE 窗口或休眠倒计时内维持常驻重连。休眠关闭的调试模式保留有界退避链路自愈。STA 的
DHCP hostname 为 ASCII `smart-fridge-magnet`；中文产品名用于 BLE 广播。

GPIO4/PREV、GPIO5/FN、GPIO6/NEXT 都通过 EXT1 `ANY_LOW` 从深睡唤醒：

- PREV/NEXT 唤醒后各轮播一次本地菜单，不自动开 BLE、不联网。
- FN 短按打开 60 秒 BLE 窗口，长按延长为 180 秒；连接期间保持唤醒。
- PREV 与 NEXT 同时按下不轮播；组合中包含 FN 时按 FN 控制意图处理。
- 醒着时 PREV 长按立即同步，NEXT 长按请求重新生成后同步。

NVS `config_version` 当前为 6。升级迁移保留 SSID、加密密码、manifest URL、设备 ID、
设备 Token、唤醒配置和 applied version；恢复出厂才清除这些值。

## BLE 边界

| 用途 | UUID |
| --- | --- |
| Service | `00002000-0000-1000-8000-00805f9b34fb` |
| Write / Notify | `00002001-0000-1000-8000-00805f9b34fb` |
| Notify 2 | `00002002-0000-1000-8000-00805f9b34fb` |
| Notify 3 | `00002003-0000-1000-8000-00805f9b34fb` |

客户端必须订阅三个通知特征。帧格式为
`HEAD(0x55) | CH | LEN | DATA | SUM`，一个协议帧必须通过一次带响应 GATT 写完整发送。
`FILE_NAME`、配网字符串和 Token 不附加 NUL；SSID 与 Wi-Fi 密码使用 UTF-8，其它规定为
ASCII 的字段遇到非 ASCII 时直接拒绝。

建议 MTU 247、`FILE_DATA` 每帧 180 字节。多图直传先开 `0x46` 静默接收，前几张不刷新，
最后关闭静默并发送总览；不逐张触发电子纸刷新。完整 opcode、载荷、Ack、长度上限和传输状态机
统一以 [固件 BLE 协议](../../embedded/smart_fridge_magnet/docs/ble_protocol.md) 为准。

## 变更规则

- HTTP 新字段优先设计为可选 nullable 字段，旧客户端和旧固件必须能忽略。
- manifest 顶层单张字段不得删除；修改 `items[]` 上限需要同步固件并重新烧录。
- BLE opcode、载荷或 Ack 变化必须同步 Android、固件、调试工具和协议测试。
- 数据库新增跨端字段必须提供兼容迁移，旧行按 null 或明确默认值读取。
- 任何契约修改同一提交内更新实现、测试、本文件和根目录 CHANGELOG。
