# Contributing

[简体中文](CONTRIBUTING.md) | English

Contributions are welcome through small, focused changes with matching tests.

## Development Rules

- Keep rendering in `backend/src/menu_render.py` and `film_encoder.py`; Android
  and firmware must not introduce a second `.film` encoder.
- Keep the 792 x 528, six-color, 209120-byte format synchronized across all
  three runtimes.
- Never add secrets, local databases, logs, generated media, build output, or
  private deployment details.
- Preserve upstream notices when modifying FrameFilm-derived files.

## Verification

Run the smallest commands that cover the change, then expand when a shared
contract changed:

```bash
cd backend && .venv/bin/python -m pytest && .venv/bin/ruff check src
cd android && ./gradlew testDebugUnitTest
cd embedded/smart_fridge_magnet && bash test/host/run_host_tests.sh
```

Firmware changes that touch BLE, Wi-Fi, e-paper, power, or wake-up behavior also
require an ESP-IDF build and relevant device testing.

Use [Conventional Commits](https://www.conventionalcommits.org/) such as `fix:`,
`feat:`, `docs:`, or `refactor:`. Keep pull requests scoped, explain observable
behavior, and include verification results without credentials or private URLs.
