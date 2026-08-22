"""Cross-platform byte/data contracts must stay identical in Python, Kotlin, and C."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from src import database, menu_render  # noqa: E402


ANDROID_BLE = ROOT / 'android/app/src/main/java/org/smartfridgemagnet/transfer/BleProtocol.kt'
ANDROID_MODELS = ROOT / 'android/app/src/main/java/org/smartfridgemagnet/data/Models.kt'
ANDROID_DEVICE_UI = ROOT / 'android/app/src/main/java/org/smartfridgemagnet/ui/DeviceConsoleScreen.kt'
ANDROID_DTOS = ROOT / 'android/app/src/main/java/org/smartfridgemagnet/network/BackendDto.kt'
FIRMWARE_BLE = ROOT / 'embedded/smart_fridge_magnet/components/film_service/inc/service_ble.h'
FIRMWARE_FILM = ROOT / 'embedded/smart_fridge_magnet/components/film_service/src/service_film.c'


OPCODE_NAMES = {
    'CH_FILE_NAME': 'BLE_FILM_TRANS_CH_FILE_NAME',
    'CH_FILE_LEN': 'BLE_FILM_TRANS_CH_FILE_LEN',
    'CH_FILE_DATA': 'BLE_FILM_TRANS_CH_FILE_DATA',
    'CH_FILE_START': 'BLE_FILM_TRANS_CH_FILE_START',
    'CH_FILE_STOP': 'BLE_FILM_TRANS_CH_FILE_STOP',
    'CH_FILE_DELETE': 'BLE_FILM_TRANS_CH_FILE_DELETE',
    'CH_FILE_LIST': 'BLE_FILM_TRANS_CH_FILE_LIST',
    'CH_FILE_DISPLAY': 'BLE_FILM_TRANS_CH_FILE_DISPLAY',
    'CH_FILE_DISPLAY_GET': 'BLE_FILM_TRANS_CH_FILE_DISPLAY_GET',
    'CH_CTRL_MODE': 'BLE_FILM_TRANS_CH_CTRL_MODE',
    'CH_CTRL_MODE_GET': 'BLE_FILM_TRANS_CH_CTRL_MODE_GET',
    'CH_CTRL_RESET': 'BLE_FILM_TRANS_CH_CTRL_RESET',
    'CH_CTRL_PWRREAD': 'BLE_FILM_TRANS_CH_CTRL_PWRREAD',
    'CH_CTRL_REBOOT': 'BLE_FILM_TRANS_CH_CTRL_REBOOT',
    'CH_SLEEP_ONOFF': 'BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF',
    'CH_SLEEP_ONOFF_GET': 'BLE_FILM_TRANS_CH_CTRL_SLEEPONOFF_GET',
    'CH_WIFI_ENABLE': 'BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE',
    'CH_WIFI_ENABLE_GET': 'BLE_FILM_TRANS_CH_CTRL_WIFI_ENABLE_GET',
    'CH_WIFI_SSID': 'BLE_FILM_TRANS_CH_CTRL_WIFI_SSID',
    'CH_WIFI_SSID_GET': 'BLE_FILM_TRANS_CH_CTRL_WIFI_SSID_GET',
    'CH_WIFI_PASSWORD': 'BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD',
    'CH_WIFI_PASSWORD_GET': 'BLE_FILM_TRANS_CH_CTRL_WIFI_PASSWORD_GET',
    'CH_FILM_API_URL': 'BLE_FILM_TRANS_CH_CTRL_FILM_API_URL',
    'CH_FILM_API_URL_GET': 'BLE_FILM_TRANS_CH_CTRL_FILM_API_URL_GET',
    'CH_WIFI_CONNECT': 'BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT',
    'CH_WIFI_DISCONNECT': 'BLE_FILM_TRANS_CH_CTRL_WIFI_DISCONNECT',
    'CH_WIFI_CONNECT_GET': 'BLE_FILM_TRANS_CH_CTRL_WIFI_CONNECT_GET',
    'CH_WIFI_CLEAR': 'BLE_FILM_TRANS_CH_CTRL_WIFI_CLEAR',
    'CH_DOWNLOAD_START': 'BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD',
    'CH_DOWNLOAD_STATUS_GET': 'BLE_FILM_TRANS_CH_CTRL_FILM_DOWNLOAD_STATE',
    'CH_MANIFEST_URL': 'BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL',
    'CH_MANIFEST_URL_GET': 'BLE_FILM_TRANS_CH_CTRL_MANIFEST_URL_GET',
    'CH_DEVICE_ID': 'BLE_FILM_TRANS_CH_CTRL_DEVICE_ID',
    'CH_DEVICE_ID_GET': 'BLE_FILM_TRANS_CH_CTRL_DEVICE_ID_GET',
    'CH_DEVICE_TOKEN': 'BLE_FILM_TRANS_CH_CTRL_DEVICE_TOKEN',
    'CH_STATUS_GET': 'BLE_FILM_TRANS_CH_CTRL_STATUS_QUERY',
    'CH_SYNC_NOW': 'BLE_FILM_TRANS_CH_CTRL_SYNC_NOW',
    'CH_FILE_CLEAR_ALL': 'BLE_FILM_TRANS_CH_FILE_CLEAR_ALL',
    'CH_FILE_SILENT_RECV': 'BLE_FILM_TRANS_CH_FILE_SILENT_RECV',
    'CH_FILE_DISPLAY_NOW': 'BLE_FILM_TRANS_CH_FILE_DISPLAY_NOW',
    'CH_WAKE_SCHEDULE': 'BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE',
    'CH_WAKE_SCHEDULE_GET': 'BLE_FILM_TRANS_CH_CTRL_WAKE_SCHEDULE_GET',
    'CH_POLL_INTERVAL': 'BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL',
    'CH_POLL_INTERVAL_GET': 'BLE_FILM_TRANS_CH_CTRL_POLL_INTERVAL_GET',
    'CH_PIN_FILM': 'BLE_FILM_TRANS_CH_CTRL_PIN_FILM',
    'CH_PIN_FILM_GET': 'BLE_FILM_TRANS_CH_CTRL_PIN_FILM_GET',
}

EXPECTED_SLOT_FILES = {
    'today': '1_today.film',
    'breakfast': '2_breakfast.film',
    'lunch': '3_lunch.film',
    'dinner': '4_dinner.film',
}


def _hex_constants(source: str, pattern: str) -> dict[str, int]:
    return {name: int(value, 16) for name, value in re.findall(pattern, source, re.MULTILINE)}


def test_android_and_firmware_ble_opcodes_match():
    kotlin = ANDROID_BLE.read_text(encoding='utf-8')
    header = FIRMWARE_BLE.read_text(encoding='utf-8')
    android = _hex_constants(kotlin, r'^\s*const val (CH_[A-Z0-9_]+)\s*=\s*0x([0-9A-Fa-f]+)')
    firmware = _hex_constants(
        header,
        r'^#define\s+(BLE_FILM_TRANS_CH_[A-Z0-9_]+)\s+\(0x([0-9A-Fa-f]+)\)',
    )

    assert set(android) == set(OPCODE_NAMES), '新增 Android opcode 时必须加入跨端映射'
    assert {
        name: android[name] for name in OPCODE_NAMES
    } == {
        name: firmware[c_name] for name, c_name in OPCODE_NAMES.items()
    }


def test_inventory_categories_match_android_and_backend():
    kotlin = ANDROID_MODELS.read_text(encoding='utf-8')
    enum_body = kotlin.split('enum class IngredientCategory', 1)[1].split('\n}', 1)[0]
    android_categories = re.findall(r'^\s*[A-Z_]+\("([^"]+)"', enum_body, re.MULTILINE)

    assert android_categories == database.DEFAULT_CATEGORIES
    assert len(android_categories) == 13


def test_menu_slots_match_backend_android_and_firmware():
    android_ui = ANDROID_DEVICE_UI.read_text(encoding='utf-8')
    android_dto = ANDROID_DTOS.read_text(encoding='utf-8')
    firmware = FIRMWARE_FILM.read_text(encoding='utf-8')

    full_slots = re.search(r'FULL_SLOTS\s*=\s*listOf\(([^)]+)\)', android_ui)
    assert full_slots is not None
    android_slots = re.findall(r'"([a-z]+)"', full_slots.group(1))
    firmware_slots = set(re.findall(r'"(today|breakfast|lunch|dinner)"', firmware))

    assert menu_render.SLOT_FILENAMES == EXPECTED_SLOT_FILES
    assert android_slots == list(EXPECTED_SLOT_FILES)
    assert firmware_slots == set(EXPECTED_SLOT_FILES)
    assert '@SerializedName("slot_files")' in android_dto
    assert 'val name: String = ""' in android_dto
