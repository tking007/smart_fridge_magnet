from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testsupport import disposable_token  # noqa: E402

from src import database, device_display


def test_device_timeline_covers_manifest_download_apply_and_auth(monkeypatch, tmp_path):
    monkeypatch.setenv('SFM_DATABASE_PATH', str(tmp_path / 'events.db'))
    monkeypatch.setenv('SFM_FILM_DIR', str(tmp_path / 'films'))
    database.init_db()
    device_display.init_device_tables()

    device = 'timeline-device'
    film_dir = tmp_path / 'films' / device
    film_dir.mkdir(parents=True)
    (film_dir / '1.film').write_bytes(b'film')

    conn = database.get_connection()
    conn.execute(
        'INSERT INTO devices (device_id, device_token) VALUES (?, ?)',
        (device, disposable_token('device-')),
    )
    conn.execute('''
        INSERT INTO device_display
            (device_id, version, slot, file_path, file_size, sha256)
        VALUES (?, 1, 'current', ?, 4, ?)
    ''', (device, str(Path(device) / '1.film'), '0' * 64))
    conn.commit()
    conn.close()

    assert device_display.get_manifest(device, 'https://example.test')['version'] == 1
    assert device_display.download_film(device, 1) is not None
    device_display.acknowledge_applied_version(device, 1)
    device_display.acknowledge_applied_version(device, 1)
    device_display.log_device_auth_failure(device, 'bad token', '/display/manifest')

    events = device_display.get_sync_status(device)['recent_events']
    kinds = [event['event'] for event in events]
    assert {'manifest_query', 'download', 'apply', 'auth_failed'} <= set(kinds)
    assert kinds.count('apply') == 1
