<p align="center">
  <a href="docs/assets/smart_fridge_magnet_icon.svg">
    <img src="docs/assets/smart_fridge_magnet_icon.png" alt="Smart Fridge Magnet project icon" width="160">
  </a>
</p>

<h1 align="center">Smart Fridge Magnet</h1>

<p align="center">
  <a href="README.md">简体中文</a> | English
</p>

![Smart Fridge Magnet project cover](docs/assets/smart_fridge_magnet_cover.png)

Smart Fridge Magnet is an open-source prototype for a 792 x 528 six-color
e-paper refrigerator display. An Android app manages food inventory and device
settings, a Flask backend generates and renders daily menus, and ESP32-S3
firmware downloads the rendered `.film` files over HTTPS.

> This repository is an independent open-source project. It is not affiliated
> with or endorsed by any company or device ecosystem.

## Architecture

```text
Android app
  -> Flask API: inventory, household profile, menu generation, device control
  -> backend renderer: 792 x 528 image -> 209120-byte .film file
  -> HTTPS manifest: one version containing four meal images
  -> ESP32-S3 firmware: download, verify, persist, display, deep sleep
```

The backend is the only `.film` encoder. Android previews decode the backend
artifact, and the firmware only handles files and pixels. This keeps the app
preview identical to the bytes displayed by the device.

## Features

- Food inventory, shelf-life tracking, household profiles, and meal plans.
- Configurable Chat Completions-compatible text, vision, and speech models.
- Versioned four-screen publishing for today, breakfast, lunch, and dinner.
- BLE provisioning and fallback transfer, plus HTTPS synchronization.
- ESP32-S3 deep sleep, scheduled wake-up, local cache, and OTA support.
- Browser-based hardware and BLE diagnostic tool with no build step.

## Repository Layout

```text
android/    Kotlin and Jetpack Compose app
backend/    Flask API, AI integration, rendering, scheduling, and tests
embedded/   ESP32-S3 firmware and host-side tests
hardware/   Hardware snapshot and enclosure models
docs/       Cross-platform contracts and power model
tools/      Browser-based device diagnostic tool
```

## Quick Start

### Backend

Python 3.13 is recommended.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
.venv/bin/python scripts/setup_demo_data.py
.venv/bin/python -m src.app
```

Set `AI_API_KEY`, `AI_BASE_URL`, and the three `AI_MODEL_*` values in `.env`
for a Chat Completions-compatible provider. Local development binds to
`127.0.0.1:5000` by default. See [backend/README.md](backend/README.md) for
authentication, HTTPS, persistence, and deployment details.

The full nutrition table is included at
`backend/data/nutrition/china_food_composition.json` and may be imported into
SQLite by the project maintainer. See [NOTICE.md](NOTICE.md) and retain the
dataset notice when redistributing it. You can also run
`backend/scripts/setup_demo_data.py` for a small synthetic demo dataset. Demo
household profiles and screenshots are synthetic examples, not real families.

### Android

#### Prerequisites

- [Android Studio](https://developer.android.com/studio) (recommended), or the
  standalone [Android SDK Command-Line Tools](https://developer.android.com/tools/sdkmanager).
- JDK 17 or newer; the project has been verified with OpenJDK 22. `build-apk.sh`
  checks the Java version before building.
- Install these packages through Android Studio's SDK Manager or `sdkmanager`:
  - Android SDK Platform 36 (`platforms;android-36`; `targetSdk` remains 34)
  - Android SDK Build-Tools 36 (`build-tools;36.0.0`)
  - Android SDK Platform-Tools (`platform-tools`, including `adb`)
- The first build needs access to `google()`, `mavenCentral()`, and the Gradle
  distribution service to download AndroidX, Compose, OkHttp, Coil,
  ZXing, and other Gradle-managed dependencies.

The project pins Android Gradle Plugin `8.13.2`, Kotlin `2.3.21`, and Gradle
Wrapper `8.13`, so a system-wide Gradle installation is not required. A
command-line environment can be configured as follows:

```bash
export JAVA_HOME="/path/to/jdk-17-or-newer"
export ANDROID_HOME="$HOME/Android/Sdk"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

sdkmanager --licenses
sdkmanager "platforms;android-36" "build-tools;36.0.0" "platform-tools"
```

If the SDK is not under `$ANDROID_HOME`, create the Git-ignored
`android/local.properties` file instead:

```properties
sdk.dir=/absolute/path/to/Android/Sdk
```

After the environment is ready, run:

```bash
cd android
./build-apk.sh
```

The script builds a Debug APK by default. It also supports
`./build-apk.sh release`, `./build-apk.sh debug -i`, and
`./build-apk.sh clean`. Direct Gradle commands include
`./gradlew testDebugUnitTest` and `./gradlew :app:assembleDebug`. See the
[Android development guide](android/README.md) for details.

Configure the backend URL and app token in the app's developer settings. The
app never stores an AI provider key.

### Firmware

Install ESP-IDF v6.0.2, then run:

```bash
cd embedded/smart_fridge_magnet
bash test/host/run_host_tests.sh
idf.py set-target esp32s3
idf.py build
```

The reproducible build baseline is documented in
[embedded/smart_fridge_magnet/BUILD.en.md](embedded/smart_fridge_magnet/BUILD.en.md).
Generating a local CA and flashing are covered by
[embedded/README.md](embedded/README.md), and release-device checks are listed
in [embedded/VERIFICATION.en.md](embedded/VERIFICATION.en.md). Firmware builds do not
replace device tests for e-paper refresh, BLE, Wi-Fi, wake-up, or current draw.

### Browser Diagnostic Tool

```bash
python3 -m http.server 8000 --directory tools/sfm-devtool
```

Open `http://localhost:8000` in a browser with Web Bluetooth support.

## Security

- Never commit `.env`, API keys, device tokens, private keys, databases, logs,
  generated `.film` files, or build outputs.
- Replace example credentials before deploying. For self-signed HTTPS, generate a
  deployment-specific CA and enable it only in the local firmware build.
- Keep the backend on loopback unless authentication and HTTPS are configured.
- Report vulnerabilities according to [SECURITY.en.md](SECURITY.en.md).

## Documentation

- [Cross-platform contracts](docs/contracts/README.md)
- [Backend API](backend/docs/API.md)
- [BLE protocol](embedded/smart_fridge_magnet/docs/ble_protocol.md)
- [Hardware attribution and scope](hardware/README.md)
- [Contributing guide](CONTRIBUTING.en.md)

## Acknowledgements

Thanks to the [kirmisaki/FrameFilm](https://github.com/kirmisaki/FrameFilm)
project and its contributors for publishing the FrameFilm firmware and hardware
design. Parts of this project's ESP32-S3 firmware and hardware snapshot derive
from FrameFilm and were adapted for the Smart Fridge Magnet six-color e-paper,
Android, backend synchronization, and low-power workflow.

See [Notices and Attribution](NOTICE.en.md) and the
[firmware upstream notice](embedded/smart_fridge_magnet/NOTICE.en.md) for the
import baselines, hardware snapshot commit, licenses, and modification scope.

## License

This repository is distributed under the GNU General Public License v3.0 or
later; see [LICENSE](LICENSE). Bundled upstream hardware and firmware retain
their copyright notices and GPL terms. See [NOTICE.en.md](NOTICE.en.md) for
attribution.
