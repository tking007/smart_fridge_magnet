"""Regression check for committing firmware versions only after EPD refresh."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WIFI = ROOT / "embedded/smart_fridge_magnet/components/film_service/src/service_wifi.c"
FILM = ROOT / "embedded/smart_fridge_magnet/components/film_service/src/service_film.c"


def _function(source: str, name: str, next_name: str) -> str:
    return source[source.index(name):source.index(next_name, source.index(name))]


def test_version_is_persisted_only_after_epd_refresh():
    wifi = WIFI.read_text(encoding="utf-8")
    film = FILM.read_text(encoding="utf-8")

    single = _function(
        wifi,
        "static esp_err_t wifi_download_film_streaming(const manifest_t *m)\n{",
        "static esp_err_t wifi_download_one_item(const manifest_item_t *it)\n{",
    )
    film_set = _function(
        wifi,
        "static esp_err_t wifi_download_film_set(const manifest_t *m)\n{",
        "void service_wifi_display_completed(const char *filename)\n{",
    )
    completed = _function(
        wifi,
        "void service_wifi_display_completed(const char *filename)\n{",
        "static esp_err_t wifi_sync_once_run(void)\n{",
    )
    display = _function(
        film,
        "static void film_display_event(uint32_t file_id)\n{",
        "static void film_next_event(void)\n{",
    )

    assert "wifi_pending_display_arm(" in single
    assert "wifi_pending_display_arm(" in film_set
    assert "service_param_set_last_applied_version" not in single
    assert "service_param_set_last_applied_version" not in film_set
    assert "service_param_set_last_applied_version" in completed
    assert display.index("hal_epd_display_film(") < display.index("service_wifi_display_completed(")
