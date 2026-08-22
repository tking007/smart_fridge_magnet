# Changelog

[简体中文](CHANGELOG.md) | English

All notable changes to Smart Fridge Magnet are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Renamed the project to Smart Fridge Magnet / 智能冰箱贴.
- Replaced organization-specific package names, services, domains, model names,
  device identities, and deployment references with public configuration.
- Removed non-public project materials, deployment artifacts, generated output,
  local data, and historical Git metadata.
- Restored the nutrition table and documented its import path and intended use.
- Made the default firmware trust only the ESP-IDF public CA bundle; local
  self-signed CAs now require an explicit local build configuration.
- Made BLE control characteristics require encrypted Secure Connections bonding,
  and clear bonds during factory reset.
- Restored the food/category SVGs, derived Android PNGs, and role PNGs that the
  project owner confirmed come from public sources, with their mapping notes.
- Protected diagnostic health endpoints with the business token while keeping
  `/healthz` as the minimal liveness probe.
- Added size and content bounds for image uploads, barcode responses, and error
  output; device tokens now use hashed verifiers.
- Updated Android to Kotlin 2.3.21 / Compose BOM 2025.08.00, removed unused
  dependencies and storage permissions, and restored food icon integration.
- Changed remote SD-card formatting to a safe rejection and derived firmware
  serial numbers from each device MAC.

### Added

- Added public contribution, security, license, and attribution documents.
- Added a public firmware build baseline, device verification checklist, and
  version gate script.
- Added GitHub Actions checks for the backend, Android, firmware host suite, and
  repository structure.
