# Documentation

[简体中文](README.md) | English

The repository keeps one authoritative document for each topic:

| Topic | Document |
| --- | --- |
| Cross-platform fields, authentication, BLE, manifest, and `.film` | [contracts/README.md](contracts/README.md) |
| Android build and screen responsibilities | [../android/README.md](../android/README.md) |
| Backend configuration, API, and deployment | [../backend/README.md](../backend/README.md) |
| Nutrition data attribution and import | [../NOTICE.en.md](../NOTICE.en.md) / [demo data script](../backend/scripts/setup_demo_data.py) |
| Firmware build, synchronization, and low power | [../embedded/README.md](../embedded/README.md) |
| Firmware build baseline and device verification | [BUILD](../embedded/smart_fridge_magnet/BUILD.en.md) / [verification checklist](../embedded/VERIFICATION.en.md) |
| BLE byte protocol | [../embedded/smart_fridge_magnet/docs/ble_protocol.md](../embedded/smart_fridge_magnet/docs/ble_protocol.md) |
| Power model and measurement boundaries | [power-model.md](power-model.md) |
| Hardware origin and prototype scope | [../hardware/README.md](../hardware/README.md) |
| Release changes | [../CHANGELOG.en.md](../CHANGELOG.en.md) |

Update the cross-platform contract with any protocol change. Keep executable
commands in the owning component README and link to them instead of duplicating
instructions.
