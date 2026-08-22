from datetime import datetime

from src import scheduler as sched


def test_daily_rotation_waits_until_publish_time_and_is_idempotent(monkeypatch):
    state = {'dirty': True, 'revision': 3, 'last_generated_date': None}
    calls = []
    marked = []
    completed = set()

    monkeypatch.setattr(sched.settings_store, 'daily_publish_time', lambda: '06:30')
    monkeypatch.setattr(sched.db, 'get_daily_plan_state', lambda: state.copy())
    monkeypatch.setattr(sched.db, 'get_all_members', lambda: [{}, {}])
    monkeypatch.setattr(sched.db, 'get_daily_plan_progress',
                        lambda date, revision: completed.copy())
    monkeypatch.setattr(sched.db, 'mark_daily_plan_meal_completed',
                        lambda date, revision, meal: not completed.add(meal))
    def mark_generated(date, revision):
        marked.append((date, revision))
        state.update(dirty=False, last_generated_date=date)
        return True
    monkeypatch.setattr(sched.db, 'mark_daily_plan_generated', mark_generated)
    monkeypatch.setattr(sched, 'add_recipe_notification', lambda **kwargs: None)

    def generate(meal_type, people_count, user_input=''):
        calls.append((meal_type, people_count, user_input))
        return {'action': 'recommend', 'reply': 'ok', 'data': {}}

    monkeypatch.setattr(sched, 'generate_recommendation', generate)
    scheduler = sched.TaskScheduler()

    assert scheduler._rotate_daily_menu_if_due(datetime(2026, 7, 31, 6, 29)) is False
    assert calls == []
    assert scheduler._rotate_daily_menu_if_due(datetime(2026, 7, 31, 6, 30)) is True
    assert [call[0] for call in calls] == ['早餐', '午餐', '晚餐']
    assert all(call[1] == 2 and '库存已变化' in call[2] for call in calls)
    assert marked == [('2026-07-31', 3)]

    assert scheduler._rotate_daily_menu_if_due(datetime(2026, 7, 31, 8, 0)) is True
    assert len(calls) == 3


def test_daily_rotation_keeps_dirty_when_any_meal_fails(monkeypatch):
    marked = []
    calls = []
    completed = set()
    notifications = []
    state = {'dirty': True, 'revision': 7, 'last_generated_date': '2026-07-30'}

    monkeypatch.setattr(sched.settings_store, 'daily_publish_time', lambda: '06:30')
    monkeypatch.setattr(sched.db, 'get_daily_plan_state', lambda: state.copy())
    monkeypatch.setattr(sched.db, 'get_all_members', lambda: [])
    monkeypatch.setattr(sched.db, 'get_daily_plan_progress',
                        lambda date, revision: completed.copy())
    monkeypatch.setattr(sched.db, 'mark_daily_plan_meal_completed',
                        lambda date, revision, meal: not completed.add(meal))
    def mark_generated(date, revision):
        marked.append((date, revision))
        state.update(dirty=False, last_generated_date=date)
        return True
    monkeypatch.setattr(sched.db, 'mark_daily_plan_generated', mark_generated)
    monkeypatch.setattr(sched, 'add_recipe_notification',
                        lambda **kwargs: notifications.append(kwargs['meal_type']))

    lunch_attempts = 0
    def generate(meal_type, people_count, user_input=''):
        nonlocal lunch_attempts
        calls.append(meal_type)
        if meal_type == '午餐':
            lunch_attempts += 1
            if lunch_attempts == 1:
                return {'action': 'recommend_error'}
        return {'action': 'recommend', 'reply': 'ok', 'data': {}}

    monkeypatch.setattr(sched, 'generate_recommendation', generate)
    scheduler = sched.TaskScheduler()

    assert scheduler._rotate_daily_menu_if_due(datetime(2026, 7, 31, 7, 0)) is False
    assert calls == ['早餐', '午餐', '晚餐']
    assert marked == []
    assert notifications == ['早餐', '晚餐']
    assert scheduler.get_task_history()[-1]['success'] is False

    assert scheduler._rotate_daily_menu_if_due(datetime(2026, 7, 31, 7, 10)) is True
    assert calls == ['早餐', '午餐', '晚餐', '午餐']
    assert notifications == ['早餐', '晚餐', '午餐']
    assert marked == [('2026-07-31', 7)]
