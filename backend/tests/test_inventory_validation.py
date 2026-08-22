"""库存数量的 HTTP 输入边界测试。"""

import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_tmp = tempfile.mkdtemp(prefix='sfm-inventory-validation-')
os.environ['SFM_DATABASE_PATH'] = os.path.join(_tmp, 'fridge.db')
os.environ['SFM_SKIP_ENV_FILE'] = '1'
os.environ['SFM_API_TOKEN'] = ''
os.environ['SFM_HOST'] = '127.0.0.1'
os.environ['SFM_ENABLE_SCHEDULER'] = '0'
os.environ['SFM_AUTO_PUBLISH'] = '0'

from src.app import app  # noqa: E402


def _item(**amount):
    return {'name': '测试食材', 'category': '其他', 'unit': '个', **amount}


def test_invalid_inventory_amounts_return_400():
    client = app.test_client()
    for amount in ({'quantity': 'abc'}, {'quantity': 0, 'weight_grams': 'abc'}):
        response = client.post('/api/inventory', json=_item(**amount))
        assert response.status_code == 400, response.get_json()
        assert response.get_json()['code'] == 3005


def test_invalid_batch_amount_is_rejected_atomically():
    response = app.test_client().post('/api/inventory/batch', json={
        'items': [_item(quantity=1), _item(quantity='abc')],
    })
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['code'] == 3005
