import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import database as db
from src.agent import parse_deduction_amount
from src.validators import validate_inventory_item


def test_inventory_quantity_weight_category_and_shelf_contract(monkeypatch, tmp_path):
    monkeypatch.setenv('SFM_DATABASE_PATH', str(tmp_path / 'inventory.db'))
    db.init_db()

    # Existing rows stored as g/克 are backfilled idempotently.
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO fridge_inventory (name, category, quantity, unit)
           VALUES ('面粉', '主食', 500, 'g')"""
    )
    conn.commit()
    conn.close()
    db.init_db()
    migrated = db.get_inventory()['items'][0]
    assert migrated['weight_grams'] == 500
    assert migrated['quantity'] == 0
    assert migrated['unit'] == '个'

    conn = db.get_connection()
    conn.execute(
        """INSERT INTO fridge_inventory (name, category, quantity, unit)
           VALUES ('油豆腐', '调味品', 6, '个')"""
    )
    conn.commit()
    conn.close()
    db.init_db()
    fried_tofu = next(item for item in db.get_inventory()['items'] if item['name'] == '油豆腐')
    assert fried_tofu['category'] == '豆制品'

    new_tofu_id = db.add_inventory_item('香煎油豆腐', '调味品', quantity=2)
    new_tofu = next(item for item in db.get_inventory()['items'] if item['id'] == new_tofu_id)
    assert new_tofu['category'] == '豆制品'

    item_id = db.add_inventory_item(
        '西红柿', '蔬菜', quantity=2, weight_grams=300,
        production_date='2026-07-01', shelf_life_days=7,
    )
    tomato = next(item for item in db.get_inventory()['items'] if item['id'] == item_id)
    assert tomato['expiry_date'] == '2026-07-08'
    assert '西红柿 × 300克，2个' in db.get_inventory_summary()

    weight_only = validate_inventory_item({
        'name': '牛肉', 'category': '肉类', 'weight_grams': 250,
    })
    assert weight_only['quantity'] == 0
    assert weight_only['weight_grams'] == 250

    beef_id = db.add_inventory_item('牛肉', '肉类', weight_grams=250)
    deducted = db.deduct_inventory([{'name': '牛肉', 'weight_grams': 200}])
    assert deducted[0]['unit'] == '克'
    beef = next(item for item in db.get_inventory()['items'] if item['id'] == beef_id)
    assert beef['weight_grams'] == 50

    assert parse_deduction_amount('200克') == {'weight_grams': 200}
    assert parse_deduction_amount('0.5kg') == {'weight_grams': 500}
    assert parse_deduction_amount('2个') == {'quantity': 2}

    state = db.get_daily_plan_state()
    assert db.mark_daily_plan_generated('2026-07-31', state['revision']) is True
    db.add_inventory_item('鸡蛋', '蛋类', quantity=2)
    assert db.mark_daily_plan_generated('2026-07-31', state['revision']) is False
    assert db.get_daily_plan_state()['dirty'] is True

    # The DB guard protects writers that bypass Python validation.
    conn = db.get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fridge_inventory (name, category, quantity) VALUES ('未知', '自定义类', 1)"
        )
    conn.close()


def test_recipe_inventory_excludes_non_cooking_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv('SFM_DATABASE_PATH', str(tmp_path / 'recipe.db'))
    monkeypatch.delenv('SFM_RECIPE_INCLUDE_NON_COOKING', raising=False)
    db.init_db()
    db.add_inventory_item('可乐', '饮料', quantity=1, unit='瓶')
    db.add_inventory_item('薯片', '零食', quantity=1, unit='袋')
    assert db.get_inventory_summary() == '冰箱为空'

    monkeypatch.setenv('SFM_RECIPE_INCLUDE_NON_COOKING', 'true')
    summary = db.get_inventory_summary()
    assert '可乐' in summary and '薯片' in summary


def test_inventory_write_rolls_back_when_revision_mark_fails(monkeypatch, tmp_path):
    monkeypatch.setenv('SFM_DATABASE_PATH', str(tmp_path / 'atomic.db'))
    db.init_db()
    original = db.mark_plan_dirty
    monkeypatch.setattr(
        db, 'mark_plan_dirty',
        lambda conn=None: (_ for _ in ()).throw(sqlite3.OperationalError('mark failed'))
    )
    with pytest.raises(sqlite3.OperationalError, match='mark failed'):
        db.add_inventory_item('鸡蛋', '蛋类', quantity=2)
    monkeypatch.setattr(db, 'mark_plan_dirty', original)
    assert db.get_inventory()['total'] == 0


def test_shelf_life_can_be_inferred_from_any_two_fields():
    assert db.resolve_shelf_life('2026-07-01', 30, None) == (
        '2026-07-01', 30, '2026-07-31'
    )
    assert db.resolve_shelf_life(None, 30, '2026-07-31') == (
        '2026-07-01', 30, '2026-07-31'
    )
    assert db.resolve_shelf_life('2026-07-01', None, '2026-07-31') == (
        '2026-07-01', 30, '2026-07-31'
    )
