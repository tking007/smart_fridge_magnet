# Firmware Verification Checklist

[简体中文](VERIFICATION.md) | English

Run this checklist on target hardware for every release candidate. Store actual
logs and measurements outside the repository; this file contains reusable
procedures and expected behavior only.

## Before Testing

- [ ] `idf.py --version` reports ESP-IDF v6.0.2.
- [ ] `bash embedded/smart_fridge_magnet/test/host/run_host_tests.sh` ends with
      `ALL HOST TESTS PASSED`.
- [ ] Build output, databases, logs, private keys, and APKs are absent from Git.

## Single-Cycle Device Checks

- [ ] Cold boot and FN open the BLE window; PREV/NEXT work while awake.
- [ ] BLE provisioning requires an encrypted link, and logs contain no Wi-Fi
      password or device token.
- [ ] Wi-Fi synchronization uses an authenticated HTTPS manifest.
- [ ] All four `.film` files pass size, header, and SHA-256 checks before the
      set is committed.
- [ ] Download, validation, refresh, or shutdown failure preserves the previous
      screen and does not advance the applied version.
- [ ] The device shuts down radios and enters deep sleep after synchronization.
- [ ] Power loss, reboot, and factory reset preserve the documented boundaries.

## Repeated and Low-Power Checks

- [ ] Complete at least 20 Wi-Fi update cycles, including no-update and failure
      cycles.
- [ ] Complete at least 10 BLE transfers, including disconnect or invalid-frame
      cycles.
- [ ] Heap watermark, task count, and open file handles show no sustained leak.
- [ ] FN and RTC wake from deep sleep; PREV/NEXT do not.
- [ ] Measure awake, network, refresh, and deep-sleep current with a meter; do
      not infer battery life from serial logs alone.

Record the firmware, ESP-IDF version, hardware, date, cycle counts, injected
failures, measurements, and rollback version. Never record Wi-Fi passwords,
device tokens, private keys, personal data, or absolute local paths.
