# Security Policy

[简体中文](SECURITY.md) | English

## Supported Version

Security fixes are applied to the current `main` branch.

## Reporting a Vulnerability

Do not open a public issue containing an exploitable vulnerability, credential,
private endpoint, personal data, or device token. Contact the repository owner
privately through the security reporting channel configured on the hosting
platform. Include affected components, reproduction steps, impact, and any
suggested mitigation.

If no private channel is configured yet, do not publish the repository until one
is available.

## Deployment Baseline

- Generate unique app tokens, device tokens, and TLS certificates.
- Keep AI provider keys on the backend only.
- Use HTTPS for manifest and `.film` downloads.
- Bind the backend to loopback by default; public bindings require authentication.
- Do not reuse the example values from `.env.example` in a real deployment.
- BLE provisioning characteristics require an encrypted link with Secure Connections bonding.
  The device has no display or input controls, so pairing uses NoInputNoOutput: passive sniffing
  is mitigated, but physical MITM protection is not provided.
- Household age, height, weight, allergy, dietary restriction, and health-note fields are sensitive
  family data. Demo records are synthetic; production operators are responsible for storage,
  backups, logs, and access control.

## Firmware Security Boundary

- Wi-Fi OTA uses HTTPS, a device token, image-format validation, SHA-256 integrity checking, and
  boot rollback protection.
- The public default configuration does not enable Secure Boot or Flash Encryption, and no signing
  private key is committed. Production hardware should enable these features locally with ESP-IDF,
  protect device keys, and publish only signed firmware.
- NVS provisioning fields use AES-CTR with a random nonce for at-rest obfuscation, but the default
  key is derived from firmware. This does not resist firmware extraction or active tampering. Use a
  device-unique key, AEAD, or a secure element for physical attack resistance.
- Remote SD-card formatting through BLE `0x2B` is disabled; storage cleanup requires local maintenance.
