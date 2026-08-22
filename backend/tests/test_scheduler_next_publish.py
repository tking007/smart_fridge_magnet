from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scheduler import next_daily_publish_at, next_publish_at


def test_next_publish_uses_window_start_and_rolls_past_meals_to_tomorrow():
    now = datetime(2026, 7, 31, 10, 0)
    schedule = [
        {'meal_type': 'breakfast', 'meal_time': '08:00'},
        {'meal_type': 'lunch', 'meal_time': '12:00'},
        {'meal_type': 'snack', 'meal_time': '10:30'},
    ]

    assert next_publish_at(schedule, now, 30) == datetime(2026, 7, 31, 11, 30)
    assert next_publish_at(schedule, datetime(2026, 7, 31, 13, 0), 30) == \
        datetime(2026, 8, 1, 7, 30)


def test_next_publish_ignores_invalid_rows():
    assert next_publish_at(
        [{'meal_type': 'lunch', 'meal_time': 'bad'}], datetime(2026, 7, 31, 10), 30
    ) is None


def test_next_daily_publish_uses_today_then_rolls_to_tomorrow():
    now = datetime(2026, 7, 31, 6, 0)
    assert next_daily_publish_at(now, '06:30') == datetime(2026, 7, 31, 6, 30)
    assert next_daily_publish_at(datetime(2026, 7, 31, 6, 30), '06:30') == \
        datetime(2026, 8, 1, 6, 30)


def test_next_daily_publish_rejects_invalid_time():
    now = datetime(2026, 7, 31, 6, 0)
    assert next_daily_publish_at(now, '24:00') is None
    assert next_daily_publish_at(now, 'bad') is None
