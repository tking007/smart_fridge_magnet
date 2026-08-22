# Firmware Build Baseline

[简体中文](BUILD.md) | English

This document records the public ESP-IDF, hardware, and partition baseline. It
contains no personal paths, internal environment details, or deployment
addresses.

## Toolchain

| Item | Value |
| --- | --- |
| ESP-IDF | v6.0.2 |
| Target | `esp32s3` |
| Flash | 4 MB, QIO, 80 MHz |
| PSRAM | 2 MB Quad, 80 MHz |
| Firmware | 0.19.3 |

The version pin is [`.espidf-version`](.espidf-version). Confirm that
`idf.py --version` reports v6.0.2 before building.

## Partition Table

[`partitions.csv`](partitions.csv) is installed at offset `0x8000`:

| Partition | Type | Size |
| --- | --- | ---: |
| `nvs` | NVS | 128 KiB |
| `otadata` | OTA data | 8 KiB |
| `phy_init` | PHY | 4 KiB |
| `ota_0` | App OTA | 1920 KiB |
| `ota_1` | App OTA | 1920 KiB |

User `.film` files live on external storage and do not consume either app
partition.

## Build, Test, and Flash

```bash
cd embedded/smart_fridge_magnet
bash test/host/run_host_tests.sh
idf.py set-target esp32s3
idf.py build
./tools/preflash_check.sh
idf.py -p <PORT> flash monitor
```

`build/`, `managed_components/`, `sdkconfig.old`, local certificates, and
server keys are generated or deployment-specific files and must not be
committed. The default build trusts the ESP-IDF public CA bundle. See
[`certs/README.md`](certs/README.md) for an explicit local self-signed setup.

Host tests and a successful ESP-IDF build do not replace hardware verification
of e-paper refresh, BLE, Wi-Fi, deep sleep, and current draw.
