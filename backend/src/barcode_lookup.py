"""条码 → 商品名，数据来自 Open Food Facts。

用真实数据库而不是问模型：条码是数据库主键，不是可以推理出来的东西，而语言模型对长数字串
的记忆极不可靠、失败方式是自信地编一个，编出来的名字会原样写进库存并进入配菜 prompt。

OFF 对国产商品覆盖率低（2026-08 抽测 15 个中国大陆条码：4 个查到，其中只有 1 个返回可
直接使用的商品名；其余是英文名或品牌碎片）。因此本模块的定位是**偶尔能省一次手输的快捷
方式**，不是主路径：
  - 查不到就明确回「不知道」，让客户端回落拍照识别（视觉模型看实物，对国产商品更可靠）；
  - 查到了也只当**候选**给用户确认，不静默入库；
  - 整个功能可用 SFM_BARCODE_LOOKUP=false 关掉，关掉后接口回 503 并给出明确原因，
    而不是假装查不到。

数据许可：Open Food Facts 数据以 ODbL 授权开放，商品名等事实性字段可自由使用。
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import config
from .logger import get_logger

logger = get_logger(__name__)

#: 只取需要的字段。OFF 的完整商品记录有数百个字段、单条可达数十 KB，
#: 全量拉取纯属浪费带宽与解析时间。
_FIELDS = 'product_name,product_name_zh,product_name_en,brands,categories_tags,quantity'

_ENDPOINT = 'https://world.openfoodfacts.org/api/v2/product/{code}.json?fields=' + _FIELDS

#: OFF 要求带可识别的 User-Agent，否则可能被限流。
_USER_AGENT = 'SmartFridgeMagnet/1.0 (https://github.com/smart-fridge-magnet)'

#: 超时。条码查询是用户扫完码正在等的前台操作，宁可快速失败让他手输，
#: 也不要让他对着转圈等一个大概率查不到的结果。
_TIMEOUT_SECONDS = 6

# API 只请求 6 个文本字段；512 KiB 已远高于正常响应，同时阻止第三方无限响应耗尽内存。
_MAX_RESPONSE_BYTES = 512 * 1024

#: OFF 的 categories_tags 到本项目分类的映射。
#: 只映射能明确对上的；对不上的一律归「其他」而不是猜 —— 分类错了会影响保质期默认值。
_CATEGORY_RULES = (
    (('en:milk', 'en:dairy', 'en:yogurt', 'en:cheese', 'en:creams'), '乳制品'),
    (('en:meat', 'en:poultry', 'en:beef', 'en:pork', 'en:sausages', 'en:fishes',
      'en:seafood'), '肉类'),
    (('en:vegetables', 'en:legumes', 'en:fresh-vegetables'), '蔬菜'),
    (('en:fruits', 'en:fresh-fruits'), '水果'),
    (('en:eggs',), '蛋类'),
    (('en:beverages', 'en:waters', 'en:juices', 'en:teas', 'en:coffees'), '饮料'),
    (('en:condiments', 'en:sauces', 'en:salts', 'en:spices', 'en:vinegars',
      'en:vegetable-oils', 'en:fats'), '调料'),
    (('en:cereals', 'en:rice', 'en:pastas', 'en:breads', 'en:flours',
      'en:breakfast-cereals'), '主食'),
    (('en:frozen-foods',), '冷冻食品'),
)


def enabled() -> bool:
    """条码查询是否开启。关掉后接口明确报「功能已关闭」，不伪装成查不到。"""
    return config.barcode_lookup_enabled()


def _map_category(tags) -> str:
    """把 OFF 的分类标签映射到本项目分类；对不上返回「其他」。

    从后往前找：OFF 的 categories_tags 是从粗到细排列的（beverages → waters），
    细的那个信息量更大。
    """
    for tag in reversed(list(tags or [])):
        key = str(tag).strip().lower()
        for needles, category in _CATEGORY_RULES:
            if any(needle in key for needle in needles):
                return category
    return '其他'


def _pick_name(product: dict) -> str:
    """挑一个能给中文用户看的商品名。

    优先中文字段，退而用通用 product_name。**不拿 brands 当商品名** ——
    实测有条目只有品牌碎片（"cremica"），把它当商品名写进库存，用户在冰箱里
    看到的是一个不知所指的词，而配菜时它也不对应任何食材。
    """
    for key in ('product_name_zh', 'product_name', 'product_name_en'):
        name = str(product.get(key) or '').strip()
        if name:
            return name
    return ''


def _looks_like_junk(code: str, name: str) -> bool:
    """明显是垃圾条目的结果。

    Open Food Facts 是社区众包库，里面存着测试数据与乱填的条目，而查询端**会补零归一**：
      - `99999999999` 被补成 `0099999999999`，匹配到 "Sugo alle Melanzone"
      - `12345678` 返回 "dsdsds"
    也就是说**扫错一位的条码可能静默返回一个看似合理的商品名**。这是这个数据源的
    固有性质，不是偶发 bug，所以必须在这里挡一道，并且无论如何都要让用户确认。

    只挡「码本身明显不是真实商品码」这一类，不试图判断商品名对不对 ——
    那需要另一份权威数据，而如果有那份数据就不必用 OFF 了。
    """
    digits = code.strip()
    # 全同一个数字（0000…、9999…）不可能是真实的 GS1 码。
    if len(set(digits)) == 1:
        return True
    # 纯递增/递减序列（12345678、87654321）是典型的手工测试数据。
    ascending = all(int(b) - int(a) == 1 for a, b in zip(digits, digits[1:]))
    descending = all(int(a) - int(b) == 1 for a, b in zip(digits, digits[1:]))
    if ascending or descending:
        return True
    # 商品名过短或没有字母数字（"x"、"??"）——众包库里的占位条目。
    stripped = name.strip()
    if len(stripped) < 2:
        return True
    return False


def lookup(code: str) -> dict:
    """查一个条码。

    @return 三种形态之一：
        {'found': True, 'name': str, 'category': str, 'quantity': str, 'source': 'openfoodfacts'}
        {'found': False, 'reason': 'not_in_database'}
        {'found': False, 'reason': 'lookup_failed', 'detail': str}   网络或解析问题

    刻意把「库里没有」与「查询失败」分开：前者重试没用（该让用户拍照），
    后者过一会儿可能就好了，而两者在界面上该说不同的话。
    """
    url = _ENDPOINT.format(code=urllib.parse.quote(code, safe=''))
    request = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                logger.warning('条码查询响应过大：%s', code)
                return {'found': False, 'reason': 'response_too_large'}
            payload = json.loads(raw.decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        # OFF 对未收录的条码也可能回 404 而不是 status=0。
        if exc.code == 404:
            return {'found': False, 'reason': 'not_in_database'}
        logger.warning('条码查询 HTTP 错误：%s code=%s', code, exc.code)
        return {'found': False, 'reason': 'lookup_failed', 'detail': f'HTTP {exc.code}'}
    except Exception as exc:
        # 网络不通、超时、返回不是 JSON 都归到这里。查询失败不该把整个加食材流程带崩。
        logger.warning('条码查询失败：%s %s', code, exc)
        return {'found': False, 'reason': 'lookup_failed', 'detail': str(exc)[:120]}

    if payload.get('status') != 1:
        return {'found': False, 'reason': 'not_in_database'}

    product = payload.get('product') or {}
    name = _pick_name(product)
    if not name:
        # 收录了但没有商品名（只有品牌或营养数据）。对使用者而言等于没查到，
        # 而回一个空名字会让客户端把空串写进库存。
        logger.info('条码已收录但没有可用商品名：%s', code)
        return {'found': False, 'reason': 'not_in_database'}

    if _looks_like_junk(code, name):
        logger.info('条码查到但判定为众包库垃圾条目，按未收录处理：%s -> %r', code, name)
        return {'found': False, 'reason': 'not_in_database'}

    return {
        'found': True,
        'name': name,
        'category': _map_category(product.get('categories_tags')),
        'quantity': str(product.get('quantity') or '').strip(),
        'source': 'openfoodfacts',
    }
