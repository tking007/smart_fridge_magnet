# 智能冰箱贴 API 文档

## 基础信息

- 基础URL: `http://localhost:5000/api`
- 请求格式: JSON
- 响应格式: JSON
- 字符编码: UTF-8

---

## 目录

1. [对话接口](#对话接口)
2. [拍照识别](#拍照识别)
3. [库存管理](#库存管理)
4. [智能查询](#智能查询)
5. [家庭画像](#家庭画像)
6. [菜谱推荐](#菜谱推荐)（含 `GET /recommend/latest` 与 `GET|PUT /meal-schedule`）
7. [通知系统](#通知系统)
8. [定时任务](#定时任务)（含 `POST /scheduler/trigger/publish`）
9. [渲染与版式](#渲染与版式)
10. [库存历史](#库存历史)
11. [健康检查](#健康检查)

> 设备显示相关接口（上传 / manifest / 按文件名下载 / `request-refresh` / `prefs` /
> `sync-status`）以 [跨端契约](../../docs/contracts/README.md) 为准。

---

## 对话接口

### POST /chat

智能对话接口，支持本地语义理解优先。

**请求:**
```json
{
    "message": "用户输入",
    "session_id": "会话ID（可选）"
}
```

**响应:**
```json
{
    "handled_locally": true,
    "intent": {...},
    "response": "回复文本",
    "results": [...]
}
```

**示例:**
```bash
# 营养查询（本地处理）
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "蛋白质高的食物"}'

# 菜谱推荐（调用AI）
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "推荐晚餐，3个人吃"}'
```

---

## 拍照识别

### POST /recognize

拍照识别食材。

**请求:**
```
Content-Type: multipart/form-data

image: 文件（必需）
auto_save: true/false（可选，默认true）
```

**响应:**
```json
{
    "success": true,
    "items": [...],
    "duplicates": [...],
    "new_items": [...],
    "message": "识别结果消息",
    "quality_info": {...},
    "need_confirm": false
}
```

**示例:**
```bash
# 自动保存
curl -X POST http://localhost:5000/api/recognize \
  -F "image=@photo.jpg"

# 需要确认
curl -X POST http://localhost:5000/api/recognize \
  -F "image=@photo.jpg" \
  -F "auto_save=false"
```

### POST /recognize/confirm

确认识别结果。

**请求:**
```json
{
    "items": [
        {
            "name": "鸡蛋",
            "category": "蛋类",
            "quantity": 6,
            "unit": "个",
            "expiry_date": "2026-08-20"
        }
    ],
    "photo_path": "/tmp/xxx.jpg"
}
```

### POST /recognize/check

仅检查图片质量。

**请求:**
```
Content-Type: multipart/form-data

image: 文件
```

**响应:**
```json
{
    "valid": true,
    "errors": [],
    "warnings": [],
    "info": {
        "file_size_mb": 2.5,
        "width": 1920,
        "height": 1080,
        "brightness": 128.5,
        "is_blurry": false
    }
}
```

---

## 库存管理

### GET /inventory

获取库存列表。

**查询参数:**
- `format`: "summary" 或 "full"（默认full）
- `category`: 按分类筛选
- `page`: 页码（默认1）
- `page_size`: 每页数量（默认50，最大200）

**响应:**
```json
{
    "items": [...],
    "total": 45,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
    "expiring_soon": [...]
}
```

### POST /inventory

添加库存项。

**请求:**
```json
{
    "name": "鸡蛋",
    "category": "蛋类",
    "quantity": 10,
    "unit": "个",
    "production_date": "2026-07-20",
    "expiry_date": "2026-08-20"
}
```

**分类枚举:**
蔬菜/水果/肉类/海鲜/蛋类/乳制品/豆制品/主食/调味品/饮料/零食/冷冻食品/其他

### POST /inventory/batch

批量添加库存项。

**请求:**
```json
{
    "items": [
        {"name": "鸡蛋", "category": "蛋类", "quantity": 10},
        {"name": "牛奶", "category": "乳制品", "quantity": 2}
    ]
}
```

### PUT /inventory/:id

更新库存项（支持乐观锁）。

**请求:**
```json
{
    "quantity": 8,
    "expiry_date": "2026-08-25",
    "version": 1
}
```

### DELETE /inventory/:id

删除库存项（软删除）。

### POST /inventory/deduct

扣减库存（FIFO先进先出）。

**请求:**
```json
{
    "items": [
        {"name": "鸡蛋", "quantity": 2},
        {"name": "牛奶", "quantity": 1}
    ]
}
```

### GET /inventory/expiring

获取即将过期的食材。

**查询参数:**
- `days`: 天数（默认3）

### GET /inventory/categories

获取分类列表。

**查询参数:**
- `type`: "preset" 或 "used"（默认preset）

---

## 智能查询

### POST /smart/query

智能查询接口。

**请求:**
```json
{
    "query": "蛋白质高的食物"
}
```

**响应:**
```json
{
    "type": "nutrition",
    "intent": {"field": "protein", "order": "DESC", "label": "高蛋白"},
    "keywords": ["蛋白质"],
    "confidence": "high",
    "handled_locally": true,
    "results": [...],
    "suggestions": [...]
}
```

### GET /smart/nutrition

营养排序查询。

**查询参数:**
- `field`: 排序字段（protein/fat/energy_kcal/fe/ca/vitamin_a/vitamin_c）
- `order`: 排序方向（ASC/DESC，默认DESC）
- `limit`: 返回数量（默认5）

### POST /smart/parse

仅解析意图。

---

## 家庭画像

### GET /profile

获取家庭画像。

**响应:**
```json
{
    "members": [...],
    "meal_schedule": [...],
    "summary": "画像摘要"
}
```

### POST /profile

用**自然语言**更新家庭画像（走模型抽取）。

**请求:**
```json
{
    "message": "家里3口人，爸爸不吃辣，孩子不吃苦瓜"
}
```

### PUT /profile/members

用 App 的成员列表**整体替换**家庭画像（结构化，不打模型）。

**请求:**
```json
{
  "members": [
    {"name": "爸爸", "age": 40, "height": 175, "weight": 70,
     "allergies": ["花生"], "dislikes_main": ["苦瓜"],
     "dislikes_ingredient": [], "dislikes_taste": ["辣"], "health_notes": "成人，均衡营养"}
  ]
}
```

**响应:**
```json
{"success": true, "member_count": 1, "allergens": ["花生"], "summary": "画像摘要"}
```

为什么需要它：在它出现之前画像**只能**通过上面那个自然语言接口写入，而 App 的成员页是纯本地
存储、从不上行 —— 用户在 App 里登记的过敏与忌口对推荐**毫无影响**，界面上看着配好了，生成
菜谱时后端根本不知道，而两端都不报错。

几个约束：

- **整体替换**（故用 `PUT`，幂等）。用户在成员页删掉一位成员或去掉一条忌口后必须立刻在推荐
  上生效；只增不减的合并会让后端继续按一份用户已经看不到的旧记录避让食材，而这种不一致在
  界面上完全无从发现。代价是通过对话补充的信息会被下一次同步覆盖，但那次覆盖是用户刚刚显式
  编辑的结果，方向可解释。
- 写入在**一个事务**里（先删后插）。不回滚的话，一次写库失败会把家庭成员清空，后续推荐**完全
  失去忌口与过敏约束** —— 一个写库错误演变成安全问题。
- **校验先于写库**：`members` 非数组 / 某成员无名字 / 列表字段传了字符串 / 超过 20 人一律
  `400`，且此时不会动到已有成员。
- 响应回显 `allergens`：这是 App 能自证「我填的过敏确实被后端收下了」的唯一途径。
- `allergies` 会被汇总置顶进推荐 prompt（菜是一锅做给全家吃的，分餐做不到），并在生成后做
  一次校验，命中就整套重生成；两次都躲不开时**宁可不给推荐**。

---

## 改菜单

三个接口职责严格分开，**`revise` 只算、`apply` 只写**。合成一个的话，用户看过 diff 点「确认」
时会再调一次模型，而第二次的结果与他刚看过的那份不是同一个 —— 确认按钮就变成了抽奖。

### POST /meal-plan/revise

说一句话改某一餐的菜单。**打模型，不落库。**

**请求:**
```json
{"meal_type": "晚餐", "instruction": "把红烧肉换成清淡点的"}
```

**响应（成功）:**
```json
{
  "success": true, "meal_type": "晚餐", "people_count": 3,
  "before": ["红烧肉", "清炒上海青"],
  "after": ["冬瓜排骨汤", "清炒上海青"],
  "added": ["冬瓜排骨汤"], "removed": ["红烧肉"],
  "plan": { "dishes": [...], "total_time": "50分钟", "note": "..." },
  "missing_ingredients": ["冬瓜"],
  "base_plan_id": 42
}
```

`base_plan_id` 是这次改动的**基准记录 id**，确认时必须原样回传给 `apply`（乐观并发令牌）。
不带它去 `apply`，用户看 diff 的这几十秒里发生的任何写入 —— 餐前定时推荐、另一台手机、
他自己刚删的那道菜 —— 都会被这次确认静默盖掉，而两边都提示成功。

**响应（业务态，HTTP 仍 200）:**

| `reason` | 含义 | 客户端该做什么 |
| --- | --- | --- |
| `no_plan` | 这一餐还没有菜单 | 引导去生成，而不是显示报错 |
| `allergen_blocked` | 改后的菜单命中家里的过敏源 | 提示换个说法；`violations` 指出是哪道菜命中哪个过敏源 |

`instruction` 上限 200 字（超限 `400`）：防的是把整段聊天记录塞进来，那会挤掉 prompt 里的
库存与画像，而挤掉画像意味着**过敏约束也一起没了**。

`added` / `removed` 用集合差算，不是逐位比 —— 菜品顺序变了不算改动，按位比会把整份都标成变化。

### POST /meal-plan/apply

把改好的方案落库。**不打模型。**

**请求:**
```json
{"meal_type": "晚餐", "plan": { "dishes": [...] }, "people_count": 3, "base_plan_id": 42}
```

`plan` 必须是 `revise` 原样返回的那份。客户端**不要**用强类型对象接一遍再序列化回来 ——
未声明的字段（`steps`、`ingredients` 明细）会静默丢失，落库的菜谱就没有做法了。

- **`base_plan_id` 是乐观并发令牌**，取 `revise` 响应里的同名字段。这一餐当前最新的待确认
  记录不再是它时回 **`409` + `reason=stale_base`**，客户端应提示「菜单变过了，请重新调整」
  并刷新当前菜单。`409` 与 `400` 必须分开引导：前者重新 `revise` 一次就能继续，后者再试
  一百次也一样。省略该字段仍然放行（兼容老客户端与脚本），此时退化成无条件覆盖。
- **独立做结构与过敏校验**，不因为 `revise` 查过就跳过：表单式删菜不经过 `revise`，客户端也
  可以直接调这个接口。信任客户端的结果是电子纸上出现一份没人审过的菜单。校验不通过回 `400`
  （结构错 `reason` 缺省、命中过敏源 `reason=allergen_blocked`）。
- 一餐最多 6 道菜。模型偶尔会给出十几道，落到 792×528 的电子纸上排不下，而截断发生在渲染层
  时用户已经确认过那份看起来很丰盛的菜单了。
- 落库是**插入新记录**而不是原地改：`meal_plan` 是「这一餐生成过哪些方案」的流水，下发任务读
  最新一条。原地改会让已经下发到设备上的那一版在库里被悄悄替换，之后再也无法解释「设备上
  显示的是哪一份」。
- **落库后不自动下发**：要不要现在让设备换画面是另一个决定（用户可能还想接着改）。需要时另调
  `POST /scheduler/trigger/publish`。

### POST /meal-plan/remove-dish

删掉一道菜。**不打模型。**

**请求:**
```json
{"meal_type": "晚餐", "dish_name": "红烧肉"}
```

删除放在后端而不是让 App 提交改好的整份方案：客户端 DTO 只声明了要显示的字段，回传时
`steps` 等会丢 —— 落库的菜谱没有做法，而删菜这个操作看起来完全成功。后端手里有完整数据。

删到一道不剩时拒绝（`reason=would_be_empty`）：一餐没有任何菜品会渲染出一张空白画面，那不是
用户的意思（他要的是"这一餐不做了"，那是另一个操作）。

内部走 `apply` 落库并自带基准令牌（读到的那条记录 id），所以这里也可能回 **`409` +
`reason=stale_base`** —— 读取与落库之间这一餐被别处改过时，删除该失败而不是把一份旧方案
改一改再插回去。客户端刷新一次当前菜单即可重试。

---

## 条码

### GET /barcode/{code}

条码查商品。`code` 为 8–14 位数字（EAN-8 / EAN-13 / UPC）。

**响应（认出来了）:**
```json
{"success": true, "barcode": "6901234567892", "name": "可口可乐 330ml",
 "category": "饮料", "cached": false, "source": "model"}
```

**响应（认不出，HTTP 200）:**
```json
{"success": false, "barcode": "...", "reason": "unknown",
 "message": "没认出这个条码对应的商品，请改用拍照识别"}
```

这个接口能做到什么、做不到什么要说清：条码到商品名需要一份商品数据库，而本项目没有 ——
这里是让模型回答「这个条码是什么商品」。常见品牌商品多数能认出来，生僻商品与散装货认不出来。
**认不出时明确回 `success:false`，绝不编一个名字**：编出来的名字会被原样写进库存并进推荐
prompt，比认不出糟得多。

- 非数字或长度不对的 `code` 在**进模型之前**就 `400`（`reason=invalid`）。放行任意字符串等于
  允许把二维码里的 URL 送进模型问「这是什么商品」，那必然得到一个编造的答案。
- 模型说 `known=true` 但名字为空、或给出白名单外的分类时，分别按「认不出」和「归到其他」处理。
- **成败都进 `barcode_cache`**。`resolved=0` 表示「问过但认不出」，它必须与「从未查过」区分 ——
  不存这一行的话，每次扫到那个生僻商品都会重新问一遍模型、等同样的几秒，得到同样的"不知道"。

---

## 菜谱推荐

### POST /recommend

请求菜谱推荐。**会调用模型，实测约 20~30 秒。**

**请求:**
```json
{
    "meal_type": "晚餐",
    "people_count": 3
}
```

### GET /recommend/latest

读取**已经生成并落库**的最近一份方案，**不调用模型**（毫秒级）。
客户端打开菜谱页应该用这条；`POST /recommend` 只在用户显式点「重新生成」时调。

**查询参数:** `meal_type`（可选，中英文皆可）。不给就按当前钟点推断：
10 点前早餐、14 点前午餐、其余晚餐。认不出的餐次报 400。

**响应 200:**
```json
{
    "success": true,
    "plan_id": 12,
    "meal_type": "dinner",
    "meal_label": "晚餐",
    "people_count": 3,
    "created_at": "2026-07-29 09:12:44",
    "selected_plan": null,
    "age_minutes": 18,
    "stale": false,
    "plan_a": { },
    "plan_b": { }
}
```

**响应 404**（库里一份都没有，属正常业务态）:
```json
{"success": false, "error": "暂无已生成的菜谱，请先生成", "code": 4004}
```

`stale` 在方案超过 **12 小时**时为 `true`（食材大概已经动过）。
`created_at` 解析不出来时 `age_minutes` 为 `null` 且 `stale` 为 `false` ——
不因为一条脏数据把可用的菜单标成不可信。

### GET /meal-schedule

读取用餐时间与自动下发参数。

**响应:**
```json
{
    "schedule": [
        {"meal_type": "breakfast", "label": "早餐", "meal_time": "07:30", "enabled": 1}
    ],
    "lead_minutes": 30,
    "auto_publish": true,
    "lead_minutes_min": 5,
    "lead_minutes_max": 240
}
```

`enabled` 是 int 0/1。

`lead_minutes` / `auto_publish` **可写**（见 `PUT`）。取值口径是 **DB 覆盖 env**：
库里有值就以库里的为准，环境变量 `SFM_AUTO_PUBLISH_LEAD_MIN` / `SFM_AUTO_PUBLISH`
退化成「初始默认值」。

`lead_minutes_min` / `lead_minutes_max` 是 `lead_minutes` 的可调范围，随响应带出，
客户端**不要硬编码** —— 写死一份必然与后端漂移，表现是界面上选得到的值被接口拒掉。

### PUT /meal-schedule

更新用餐时间与自动下发参数。

**请求（三个字段都可选，但至少给一个）:**
```json
{
    "schedule": [{"meal_type": "lunch", "meal_time": "11:45"}],
    "lead_minutes": 45,
    "auto_publish": true
}
```

| 字段 | 类型 | 校验规则 |
| --- | --- | --- |
| `schedule` | 数组 | 非空；`meal_type` 中英文皆可（早餐/breakfast…），`meal_time` 为 `HH:MM`（`00:00`–`23:59`）；同一餐不得给两个不同时间 |
| `lead_minutes` | **JSON 整数** | `5`–`240`。下界 = 调度器的检查周期；上界 4 小时 |
| `auto_publish` | **JSON 布尔** | 只接受 `true` / `false` |

**三者都不给 → 400。** 只改提前量时不必回传整份时间表（回传反而有风险：那份数据
可能是几分钟前拉的，中间被别处改过就会被覆盖回去）；但整个请求体空着一定是客户端 bug，
静默返回 200 会让那个 bug 一直查不出来。

**类型不做宽松解析。** `lead_minutes` 必须是整数，`"45"`、`45.5`、`true` 一律 400；
`auto_publish` 必须是 JSON 布尔，`"true"`、`1`、`0` 一律 400。宽松解析会把客户端的
序列化 bug 变成一个从响应上完全看不出来的行为差异。

**`lead_minutes` 越界 → 400。** 下界 5 分钟就是调度器检查发布窗口的周期
（`settings_store.DISPLAY_PUBLISH_TICK_MINUTES`，`display_publish` job 的间隔用的是同一个
常量）。窗口判定是「距下一餐还有 `diff` 分钟，`0 <= diff <= lead` 就发」，提前量小于一个
检查周期时没有任何一跳保证落进窗口，`0` 更是要求某一跳精确命中饭点那一分钟 —— 那不是
「饭点当时才发」，而是**静默关掉了自动下发**：开关仍显示开着、`GET` 也显示开着，屏幕却
再也不换图，两端日志里没有异常。
上界 240（4 小时）不是随手取的整数：提前超过 4 小时
渲染的菜单，等设备真到饭点时库存可能已经变了，而且会与前一餐的发布窗口重叠 ——
两个窗口同时命中时后一次发布会顶掉前一次，人看到的是「午餐还没吃就换成晚餐了」。

**全有或全无**：任一字段不合法就整体返回 400 且**一个字节都不写**。校验全部做完才统一
写入，不会出现「时间表写进去了、却因为 `lead_minutes` 超界返回 400」—— 那样客户端按
失败回滚界面，库里其实已经变了，之后两边显示的东西对不上却没有任何一处报过错。
半套时间表同理：它会让自动下发时机与客户端显示的不一致，而两边都不报错。

`schedule` 只改时间，不改启用开关（`db.set_meal_schedule()` 整行替换，`enabled` 回到 1）。

响应与 `GET` 同结构，便于客户端拿返回值直接刷新界面。

### POST /confirm

确认菜谱方案。

**请求:**
```json
{
    "plan_id": 1,
    "selected_plan": "A"
}
```

---

## 通知系统

### GET /notifications

获取通知。

**查询参数:**
- `unread_only`: true/false（默认true）
- `type`: 按类型筛选（recipe/expiring/inventory_low/system）

### POST /notifications/:id/read

标记通知为已读。

### POST /notifications/read-all

标记所有通知为已读。

### POST /notifications/clear

清空通知。

**请求:**
```json
{
    "type": "recipe"  // 可选
}
```

### GET /notifications/stats

获取通知统计。

---

## 定时任务

### GET /scheduler/status

获取调度器状态。

### GET /scheduler/tasks

获取所有任务。

### GET /scheduler/history

获取执行历史。

### POST /scheduler/trigger/recommend

手动触发推荐。

**请求:**
```json
{
    "meal_type": "晚餐",
    "people_count": 3
}
```

### POST /scheduler/trigger/expiring

手动触发过期检查。

### POST /scheduler/trigger/inventory

手动触发库存检查。

### POST /scheduler/trigger/publish

手动触发一次**整套四张**画面下发（不等餐前窗口）。按每台目标设备的 `layout` 偏好渲染
今日总览 + 早/中/晚详情，一次 `upload_film_set()` 发布为**一个**新版本。

**请求:** `{"meal_type": "晚餐", "force": false, "device_id": "dev001"}`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `meal_type` | string，可选 | 中英文皆可；不给按当前时间推断 |
| `force` | bool，可选，默认 `false` | `true` 时无视 6 小时幂等窗口，照发一版 |
| `device_id` | 非空 string，可选 | 只发布到该设备；省略时使用全部配置目标设备 |

**三种结果要分清：**

| 响应 | 含义 |
| --- | --- |
| `success:true` + `published:[...]` | 已发布新版本，设备下次轮询就会换图 |
| `success:true` + `skipped:true` | 该餐次近期已发过、按幂等规则跳过 —— **不是失败**（`force:true` 时不会出现这一种）|
| `success:false` + `error` | 真的没发出去。最常见是「当天没有可用菜谱方案，且重新生成失败」，此时**刻意不发** —— 宁可让屏幕留着上一张，也不推一张空菜单上去（电子纸不会自己恢复）|

**什么时候该传 `force`。** 幂等窗口是为了约束**定时任务**：`_publish_meal_display()` 每 5 分钟
一跳，一个餐前窗口会被扫到六七次，不去重就是让设备连着下六七套 204KB×4。但它不该拦住
**人的显式操作** —— 17:00 自动发过晚餐、用户 18:00 在对话里点「用这份菜单」，回一句
「近期已下发过」就是屏幕不变、什么都没发生，而接口还是 200。所以：

- App 上人按的菜谱与控制台发布按钮传 `force:true` 和当前 `device_id`；版式页由 `PUT /devices/{id}/prefs`
  在偏好变化时自动触发该设备的整套发布；
- 定时任务那条路径**绝不传**，它要的正是幂等。

`force` 非布尔（含字符串 `"true"`、数字 `1`、`null`）一律 **400**，与 `PUT /meal-schedule`
的 `auto_publish` 同一约定：宽松解析会让客户端的序列化 bug 变成「本该幂等的调用每次都发」，
而调用方看不出任何差别。

```bash
# 人的显式操作：无视幂等窗口
curl -X POST http://127.0.0.1:5000/api/scheduler/trigger/publish \
  -H 'Content-Type: application/json' -d '{"force": true}'
```

---

## 渲染与版式

### GET /render/layouts

列出可选的画面版式。不挂在某台设备下 —— 还没绑定设备时（开箱引导、纯预览）也要能列出来。

**响应:**
```json
{"layouts": [
    {"key": "columns",     "label": "三餐分栏",   "description": "...", "orientation": "landscape"},
    {"key": "columns_big", "label": "分栏大字",   "description": "...", "orientation": "landscape"},
    {"key": "list",        "label": "单列",       "description": "...", "orientation": "landscape"},
    {"key": "list_big",    "label": "单列大字",   "description": "...", "orientation": "landscape"},
    {"key": "portrait",    "label": "竖版长列表", "description": "...", "orientation": "portrait"}
]}
```

### GET /render/preview

用**示例菜单**渲染指定版式，直接返回 PNG（`image/png`），供客户端挑版式。
示例数据写死在后端、不读库存也不打模型 —— 选版式时冰箱可能是空的、菜谱还没生成，
那时若出不了图，选版式这件事就直接卡住了。

> 别和 `GET /api/devices/{id}/display/preview.png` 搞混：那个是把**某个设备已发布的
> `.film` 反解**成 PNG（回答「设备屏幕上现在是哪张图」），这个是用示例数据渲染某套版式
> （回答「这套版式长什么样」）。两者名字很像，用途完全不同；设备侧接口见
> `backend/README.md` 的设备显示接口表。

**查询参数:**
- `layout`: `columns`（默认）/ `columns_big` / `list` / `list_big` / `portrait`，未知值报 400
- `kind`: `overview`（默认）/ `breakfast` / `lunch` / `dinner`
- `natural`: 竖版传 `1` 时返回便于阅读的 `528x792` PNG；省略时保持设备像素 `792x528`

**响应头:** `X-Film-Layout`、`X-Film-Orientation`、`Cache-Control: public, max-age=300`

返回 PNG 而不是 `.film`：后者是给固件的 4bit 索引格式，任何图片控件都显示不了。
默认竖版仍返回设备原始像素 `792x528`；App 显示时使用 `natural=1` 取得 `528x792` 的自然方向。

### POST /render/film

只渲染、不发布，直接返回 209120 字节的 `.film`（`application/octet-stream`），
带 `X-Film-Kind` / `X-Film-Sha256` 响应头。给 BLE 直传用 —— 走发布版会让设备下次联网
把同一张图再下载一遍。

请求体与 `POST /devices/{id}/display/render` 的菜单字段一致，并可额外传可选
`device_id`。传入后使用该设备保存的 `layout` 与 `layout_options`；省略时使用默认版式，
兼容旧客户端。`device_id` 不是版式副本，App 不直接提交 `layout`，避免本地选择与后端偏好漂移。

```json
{
  "device_id": "dev001",
  "kind": "overview",
  "title": "今日菜单",
  "dishes": ["早餐|小米粥", "午餐|番茄炒蛋", "晚餐|清炒时蔬"]
}
```

### POST /render/image

把任意图片（手绘涂鸦 / 相册照片）渲染成 209120 字节的 `.film`，**只渲染、不发布**。

**请求：** `multipart/form-data`

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `image` | 是 | 图片文件，PNG / JPEG / WebP 等 Pillow 能解码的格式 |
| `fit` | 否 | `cover`（默认）/ `contain` / `stretch` |

用 multipart 而非 JSON + base64：与 `POST /recognize` 一致，客户端少维护一套编码；
base64 还会把这条请求里最大的一块（图片本身）再放大 1/3。

**`fit` 语义**（与网页调试工具 `fitToImageData()` 一一对应）：
- `cover`：等比放大到填满画布，居中裁掉溢出。默认值 —— `contain` 的留白在六色电子纸上
  是一大片死白
- `contain`：等比缩到装得下，居中**留白底**
- `stretch`：直接拉伸到 792×528，不保持比例

**响应：** `200` `application/octet-stream`，**恰 209120 字节**

| 响应头 | 说明 |
| --- | --- |
| `Content-Length` | 恒为 `209120` |
| `X-Film-Sha256` | 响应体的 SHA-256，小写 hex（BLE 直传前后各算一次即可校验完整性）|
| `X-Film-Fit` | 实际生效的缩放策略（省略 `fit` 时回显 `cover`）|

**错误：**

| 状态 | 场景 |
| --- | --- |
| `400` | 缺 `image` 字段 / 未选择文件 / 无法解码 / `fit` 非法 / 解码后像素总数 > 5000 万 |
| `500` | 渲染或编码异常（后端自身 bug）|

鉴权与其余业务接口一致（`app_token`），无特殊处理。

**处理链：** `Image.open` → 像素总数上限检查（在真正解码之前）→ **EXIF 方向纠正**
（`image_processor.fix_orientation()`，手机照片普遍带 orientation）→ 若有 alpha 则
**先合成到白底**（直接 `convert('RGB')` 会让透明像素变黑）→ 按 `fit` 缩放（`LANCZOS`）
→ `film_encoder.encode_rgb_image()`。

**不做抖动（dither）**：后端 / 网页调试工具 / 固件校验三端依赖同一份纯最近色量化，
才有「调试工具里预览什么样，设备上就是什么样」这个排障前提。照片因此有明显色块感，
这是六色电子纸的固有结果。

**不设 `MAX_CONTENT_LENGTH`**：那是全局开关，会一并限制 `/recognize` 的上传。
上限只在本端点内按解码后的像素总数判。

**第一版只服务 BLE 直传**，没有对应的发布版端点（自定义图**不占 manifest slot**）。
理由见 `backend/README.md` 的「自定义图片上屏」一节与 `docs/contracts/README.md`。

---

## 库存历史

### GET /inventory/history

获取历史记录。

**查询参数:**
- `item_id`: 按库存项筛选
- `action`: 按动作筛选（add/deduct/update/delete）
- `days`: 查询最近N天（默认7）
- `limit`: 返回数量（默认100）

### GET /inventory/history/:id

获取特定库存项的历史。

### GET /inventory/history/statistics

获取统计信息。

**响应:**
```json
{
    "days": 7,
    "total_records": 50,
    "by_action": {
        "add": {"count": 20, "total_quantity": 100},
        "deduct": {"count": 30, "total_quantity": 50}
    },
    "by_source": {"manual": 30, "recipe": 20},
    "top_deducted": [...],
    "top_added": [...]
}
```

### GET /inventory/history/daily

获取每日摘要。

---

## 健康检查

### GET /health

健康检查。

**查询参数:**
- `full`: true/false（默认false，只做快速检查）

**响应（快速）:**
```json
{
    "timestamp": "2026-07-22T10:30:00",
    "overall_status": "ok",
    "checks": {
        "database": {"status": "ok", ...},
        "scheduler": {"status": "ok", ...},
        "inventory": {"status": "ok", ...}
    },
    "duration_ms": 50
}
```

**响应（完整）:**
```json
{
    "timestamp": "2026-07-22T10:30:00",
    "overall_status": "ok",
    "checks": {
        "database": {...},
        "api": {...},
        "scheduler": {...},
        "inventory": {...},
        "profile": {...},
        "nutrition": {...}
    },
    "system": {
        "platform": "Windows-10",
        "python_version": "3.13.0",
        "cpu_percent": 25.0,
        "memory_percent": 60.0
    },
    "duration_ms": 1500
}
```

### GET /health/database

数据库检查。

### GET /health/api

API检查（AI连通性）。

### GET /health/scheduler

定时任务检查。

### GET /health/inventory

库存检查。

### GET /health/profile

画像检查。

### GET /health/nutrition

营养数据检查。

---

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 409 | 版本冲突 |
| 500 | 服务器内部错误 |

**错误响应格式:**
```json
{
    "error": "错误描述",
    "code": "ERROR_CODE"  // 可选
}
```

---

## 使用示例

### 完整工作流

```bash
# 1. 添加家庭成员
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "家里3口人，爸爸不吃辣"}'

# 2. 拍照识别
curl -X POST http://localhost:5000/api/recognize \
  -F "image=@fridge.jpg"

# 3. 查看库存
curl http://localhost:5000/api/inventory

# 4. 推荐菜谱
curl -X POST http://localhost:5000/api/recommend \
  -H "Content-Type: application/json" \
  -d '{"meal_type": "晚餐", "people_count": 3}'

# 5. 确认方案
curl -X POST http://localhost:5000/api/confirm \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 1, "selected_plan": "A"}'

# 6. 查看通知
curl http://localhost:5000/api/notifications

# 7. 存活检查
curl http://localhost:5000/healthz

# 诊断检查（需要 app_token）
curl http://localhost:5000/api/health?full=true \
  -H "Authorization: Bearer $SFM_API_TOKEN"
```
