# 固件构建基线

简体中文 | [English](BUILD.en.md)

本文记录公开快照的 ESP-IDF、硬件和分区基线。它不包含个人、内部环境或真机部署地址。

## 工具链

| 项目 | 值 |
| --- | --- |
| ESP-IDF | v6.0.2 |
| 目标 | `esp32s3` |
| Flash | 4 MB，QIO，80 MHz |
| PSRAM | 2 MB Quad，80 MHz |
| 固件版本 | 0.19.3 |

版本 pin 记录在 [`.espidf-version`](.espidf-version)。首次构建前应在已加载 ESP-IDF 环境中
确认 `idf.py --version` 输出为 v6.0.2。

## 分区表

自定义分区表位于 [`partitions.csv`](partitions.csv)，偏移为 `0x8000`：

| 分区 | 类型 | 大小 |
| --- | --- | ---: |
| `nvs` | NVS | 128 KiB |
| `otadata` | OTA data | 8 KiB |
| `phy_init` | PHY | 4 KiB |
| `ota_0` | App OTA | 1920 KiB |
| `ota_1` | App OTA | 1920 KiB |

用户 `.film` 文件保存在外部存储，不占用上述 App 分区。

## 依赖

组件管理器清单在 [`main/idf_component.yml`](main/idf_component.yml)，当前固定
`espressif/led_strip` 3.0.3。依赖锁定文件为 [`dependencies.lock`](dependencies.lock)。

## 构建、测试与烧录

```bash
cd embedded/smart_fridge_magnet
bash test/host/run_host_tests.sh
idf.py set-target esp32s3
idf.py build
```

如需烧录：

```bash
./tools/preflash_check.sh
idf.py -p <PORT> flash monitor
```

`build/`、`managed_components/`、`sdkconfig.old`、本地证书和服务器密钥都是生成物或
部署配置，不应提交。默认固件使用 ESP-IDF 公共 CA bundle；局域网自签 CA 的生成和本地
menuconfig 配置见 [`certs/README.md`](certs/README.md)。

## 公开构建边界

- 宿主机测试覆盖 manifest、文件事务、参数迁移、同步决策和低功耗纯逻辑。
- ESP-IDF 构建成功不代表电子纸刷新、BLE、Wi-Fi、深睡和电流指标已经在具体硬件上验证。
- 真机验证应使用项目自己的设备、网络和证书，并不得把凭据或串口路径写入仓库。
