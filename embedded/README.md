# ESP32-S3 固件

`embedded/smart_fridge_magnet/` 是智能冰箱贴六色电子纸的 ESP-IDF 工程。固件负责 BLE 配网与
兜底传输、HTTPS manifest 同步、文件事务、电子纸刷新、三按键交互和深睡调度，不理解菜谱
JSON，也不执行中文排版。

当前固件版本为 **0.19.3**，工具链固定为 **ESP-IDF v6.0.2**。

## 硬件与格式

| 项目 | 值 |
| --- | --- |
| 主控 | ESP32-S3 mini N4R2，4MB Flash / 2MB PSRAM |
| 屏幕 | 3.68 英寸六色电子纸，SE0368-C |
| 像素 | 792 x 528 |
| `.film` | 32 字节头 + 4 bit/像素，共 209120 字节 |
| 存储 | 板载 SDnand |
| 按键 | PREV GPIO6、NEXT GPIO4、FN GPIO5，低电平按下 |

硬件来源和制造资料范围见 [../hardware/README.md](../hardware/README.md)。

## 工程结构

```text
smart_fridge_magnet/
├── main/                    app_main 与组件依赖
├── components/film_hal/     EPD、按键、电源、电池、LED、存储
├── components/film_service/ BLE、Wi-Fi、文件、显示、参数和低功耗
├── components/film_sys/     系统配置、日志和版本
├── test/host/               不依赖 ESP-IDF 的纯逻辑测试
├── docs/ble_protocol.md      BLE 字节协议
├── certs/                   固件 TLS 信任锚
└── version.txt              固件版本
```

上游来源、提交和 GPLv3 许可见工程内 `NOTICE.md` 与 `LICENSE`。

## 构建与烧录

先加载 ESP-IDF v6.0.2 环境，再执行：

```bash
cd embedded/smart_fridge_magnet
idf.py --version
idf.py set-target esp32s3
idf.py build
idf.py -p <PORT> flash monitor
```

`build/`、managed components 和 `sdkconfig.old` 是生成物，不进入 Git。ESP-IDF 版本以
`.espidf-version` 和本地工具链为准。

## 宿主机测试

```bash
cd embedded/smart_fridge_magnet
bash test/host/run_host_tests.sh
```

成功哨兵为 `ALL HOST TESTS PASSED`。套件覆盖 manifest、整套事务、参数迁移、同步门控、
URL、网络状态、置顶和每日唤醒重试等纯逻辑；SPI、电子纸 BUSY、深睡 GPIO 和真实网络仍需
真机验证。

## 唤醒与深睡状态机

```text
冷启动/复位 ─┐
FN(GPIO5) ────┼─> 初始化服务 -> 按唤醒原因执行任务 -> 等待全部活动结束
RTC 定时器 ───┘                                      |
                                                     v
                  深睡 <─ 装载 FN + 下次定时唤醒源 <─ 完整下电
```

| 唤醒源 | 唤醒后的行为 | 空闲睡眠窗口 |
| --- | --- | --- |
| 冷启动或复位 | 打开 60 秒 BLE 窗口；已配网时尝试一次同步 | 所有活动结束后 60 秒 |
| FN GPIO5，EXT1 `ANY_LOW` | 打开 60 秒 BLE 窗口，供配网、查询或传图 | 所有活动结束后 60 秒 |
| RTC 定时器 | 后台同步或切换本地餐次画面，不主动广播 BLE | 所有活动结束后 20 秒 |

当前电路只有 FN GPIO5 接在深睡期间仍供电的按键路径上，因此 EXT1 掩码为 `0x20`。PREV
GPIO6 和 NEXT GPIO4 **不能从深睡唤醒**；设备已由 FN、定时器或复位唤醒后，两键才按正常
短按/长按语义工作。冷启动不是深睡唤醒源，而是上电、复位或重启后的启动路径。

休眠计时只在以下活动全部结束后推进：BLE 未连接且未接收文件、Wi-Fi 不忙、文件事务不处于
接收/校验、电子纸未刷新、BLE 广播窗口已结束。任一活动开始都会阻止深睡；广播窗口到期时会
锁存“剩余活动一结束就睡”，不会为了重新计满空闲窗口而额外保持唤醒。

正常情况下不会强行打断活动。单次连续唤醒达到 900 秒，或电量低于 5% 且未判定为充电时，
固件进入安全关机收敛：停止 BLE 窗口并关闭 GATT、终止文件接收事务，然后继续等待 Wi-Fi、
文件和电子纸进入明确空闲态。若再过 60 秒仍有活动无法结束，则重启设备恢复到可控状态，
而不是在驱动仍工作时直接深睡。极低电量保护不受“休眠模式关闭”这个调试开关影响。

## 菜单同步

```text
计划/冷启动唤醒
  -> 当天尚未成功同步：惰性初始化 Wi-Fi，拉取 manifest
  -> 有新版本：下载四张、逐张校验、整套提交
  -> 显示当前餐次并上报 applied ACK
  -> 当天已成功同步：后续餐次只切换本地菜单
  -> 空闲后重新深睡
```

- 默认唤醒槽位为 07:05、11:35、18:05，由 App 下发的完整表覆盖。
- 正常网络下每个本地自然日只需一次成功联网；午餐和晚餐主要使用本地缓存。
- 第 1、2、3 次失败分别在 15、60、240 分钟后临时重试，第 4 次不再增加额外重试。
- 下一次唤醒取餐次、临时重试和兜底周期中的最早时间。
- SSID、加密密码、manifest URL、设备 ID/Token、唤醒表和应用版本保存在 NVS。
- 日期、当天同步标志和失败次数使用 RTC 保留区，日切和冷启动按未同步处理。
- 低功耗模式只在同步事务期间打开 Wi-Fi，事务结束立即关闭射频；路由器侧 DHCP hostname
  为 `smart-fridge-magnet`，BLE 广播名称为“智能冰箱贴”。休眠关闭的调试模式才维持
  醒着期间的有界退避重连。

设备在深睡期间不能感知网络恢复，只能在下一次计划或临时重试唤醒后恢复同步。

## 按键与 BLE

| 操作 | 深睡状态 | 醒着时的行为 |
| --- | --- | --- |
| PREV 短按 | 不能唤醒 | 显示上一张 |
| NEXT 短按 | 不能唤醒 | 显示下一张 |
| FN 短按 | 唤醒并打开 60 秒 BLE 窗口 | 打开或补足 60 秒 BLE 窗口 |
| FN 长按 | 先唤醒；首次释放不补发短按 | 延长为 180 秒 BLE 窗口 |
| PREV 长按 | 不能唤醒 | 立即同步 |
| NEXT 长按 | 不能唤醒 | 请求后端重算后同步 |

PREV 与 NEXT 同时按下不轮播；组合中含 FN 时按 FN 控制意图处理。唤醒键首次释放不补发
短按，避免一次操作轮播两张。BLE 建立连接后保持唤醒，断开或窗口结束后按空闲策略睡眠。

首次连接先由 Android 系统完成 Secure Connections bonding；自定义控制特征只允许加密链路
访问。当前硬件没有可用于核对数字的显示/输入通道，因此采用 NoInputNoOutput 配对，可防
被动嗅探但不提供物理 MITM 防护。完整 opcode、帧格式和拒绝规则见
[smart_fridge_magnet/docs/ble_protocol.md](smart_fridge_magnet/docs/ble_protocol.md)。

## 省电策略

运行期间采用以下策略压缩射频、刷屏和常供电时间：

- Wi-Fi 惰性初始化，只在同步事务期间开启；最后一个网络任务结束后立即反初始化。
- RTC 定时后台唤醒不广播 BLE；冷启动和 FN 唤醒默认开放 60 秒，FN 长按延长到 180 秒。
- 按键唤醒后的 Wi-Fi 同步失败时，睡前保证至少 30 秒 BLE 可连接窗口，供手机降级处理。
- 文件字节与当前画面相同时跳过电子纸刷新，减少约 20 秒的刷新耗电和面板损耗。
- 每日成功同步后，后续餐次优先切换 SDnand 本地缓存；失败只按 15/60/240 分钟进行三档
  临时重试，避免持续轮询网络。
- 监控任务以非阻塞节拍采集活动状态；BLE、Wi-Fi、文件接收和刷屏各自完成后再进入休眠。
- 深睡前让电子纸进入 DSLP，并通过 GPIO21 `3V3_C` 切断电子纸、WS2812 和 SDnand 等外设
  的物理供电；GPIO21 由 RTC hold 保持低电平，避免数字域掉电后引脚浮空导致漏电。

进入深睡的固定下电顺序如下：

1. 停止监控定时器，关闭并反初始化 BLE。
2. 反初始化 Wi-Fi，关闭 LED、电池 ADC 和 SDnand 驱动。
3. 在 SPI 仍可用时发送电子纸 DSLP；关机 BUSY 最多等待 2 秒，随后反初始化 EPD SPI/GPIO。
4. 反初始化按键，把 GPIO21 拉低并启用 RTC hold，切断 `3V3_C`。
5. 注册 FN 的 EXT1 唤醒和下一次 RTC 定时唤醒，再调用 `esp_deep_sleep_start()`。

如果所有唤醒源都注册失败，固件会重启而不是进入无法自行恢复的深睡。当前已通过真机串口确认
`0.19.3` 启动、EXT1 掩码 `0x20`、RTC 定时器装载、完整下电和进入深睡，且观察期内没有
立即误唤醒；深睡电流和据此推算的续航仍需用电流表实测，不能仅凭串口日志宣称达标。

## 电子纸刷新

EPD HAL 的初始化、双 pass、BUSY 等待和关机均返回 `esp_err_t`。只有两个 pass、BUSY 和关机
全部成功，服务才推进 applied version、发送完成事件和 ACK。失败保留原版本和屏幕，并记录
reset、温度、pass 1/2 传输与 BUSY、关机和总耗时。

保持以下原则：

- 画面字节未变化时跳过刷新。
- 不删除 `color_map` / `color_map1` 双 pass。
- BUSY 超时必须按失败传播，禁止把超时记成上屏成功。
- 快速波形/LUT 必须在取得面板厂商资料和真机数据后单独评估。

## HTTPS 与状态上报

manifest 和 `.film` 全程使用 HTTPS + Bearer `device_token`，固定校验长度、文件头和 SHA-256。
自签 CA 与服务器证书生成见
[smart_fridge_magnet/certs/README.md](smart_fridge_magnet/certs/README.md)。

manifest 请求会附带固件、电量、应用版本、休眠模式、唤醒槽位、兜底周期和轮询周期。后端
把它们保存为云端快照；Wi-Fi 当前连接、busy、最近错误、文件列表和配置回读只通过 BLE
实时获取。

固件 OTA 还依赖 ESP-IDF 的镜像格式校验和启动回滚；公开默认配置没有启用 Secure Boot 或
Flash Encryption，也不包含签名私钥。生产部署应在本地配置设备级密钥并只发布签名镜像。NVS
字段的 AES-CTR 仅提供静态混淆，不能抵御固件提取或主动篡改，边界说明见根目录
[`SECURITY.md`](../SECURITY.md)。

## 参数与升级

当前 NVS 参数 `config_version` 为 6。已知旧 schema 通过 `param_serialize` 迁移，并保留配网、
密钥、同步版本和唤醒配置；未知或损坏记录 fail-closed 回到默认值。置顶画面使用独立 NVS
记录，不改变主参数 blob。

## 真机验收

发布固件前应执行并保存串口或测量证据，至少包括：

- 10 次完整刷新阶段耗时及中位数/最大值。
- FN 深睡唤醒；设备醒着时的 PREV/NEXT 短按、长按、组合键、释放去重和 FN 长按。
- Wi-Fi 同步完成后睡眠、BLE 连接保活和断开后睡眠。
- 当天一次成功联网、午晚餐本地切图、断网恢复和三档重试。
- 任一下载、校验、刷新或关机失败都不推进 applied version。

完整构建基线见 [smart_fridge_magnet/BUILD.md](smart_fridge_magnet/BUILD.md)，发布候选的单轮、
连续传输和低功耗检查见 [VERIFICATION.md](VERIFICATION.md)。
