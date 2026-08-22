"""
定时任务调度器 — 增强版
支持：定时推荐、过期检查、库存提醒、任务管理
"""
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import config
from . import database as db
from . import device_display
from . import menu_render
from . import settings_store
from .agent import generate_recommendation
from .logger import get_logger
from .meals import meal_key, meal_keys_ordered, meal_label
from .notification import (
    add_recipe_notification,
    add_expiring_notification,
    add_inventory_low_notification
)

logger = get_logger('scheduler')


def layout_publish_source(source: str, layout: str, options: dict) -> str:
    """构造某个版式与自定义参数下的发布幂等键。

    版式与参数进键，改版式后重新发布才不会被「近期已下发过」挡掉。
    """
    layout = menu_render.get_layout(layout).key
    options = menu_render.normalize_layout_options(options)
    option_key = ','.join(f'{key}={options[key]}' for key in sorted(options))
    return f'{source}:render-v{menu_render.RENDER_REVISION}:layout={layout}:{option_key}'


def _utc_window_start(hours: int) -> str:
    """构造「最近 N 小时」的 UTC 起点字符串，用于和库里的 created_at 比较。

    库里所有 created_at 都是 SQLite CURRENT_TIMESTAMP 写入的 UTC 文本
    （'YYYY-MM-DD HH:MM:SS'），可以直接做字符串比较。
    """
    return (datetime.utcnow() - timedelta(hours=max(1, hours))).strftime('%Y-%m-%d %H:%M:%S')


def next_publish_at(schedule: List[Dict], now: datetime, lead_minutes: int) -> Optional[datetime]:
    """Return the next local publish-window start for a valid breakfast/lunch/dinner row."""
    candidates = []
    for meal in schedule:
        if meal_key(meal.get('meal_type')) is None:
            continue
        try:
            hour, minute = map(int, str(meal.get('meal_time', '')).split(':'))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (TypeError, ValueError):
            continue
        start = target - timedelta(minutes=lead_minutes)
        if target < now:
            start += timedelta(days=1)
        candidates.append(start)
    return min(candidates) if candidates else None


def next_daily_publish_at(now: datetime, publish_time: str) -> Optional[datetime]:
    """下一次每日更新时间；非法 HH:MM 返回 None。"""
    try:
        hour, minute = map(int, publish_time.split(':'))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return candidate if candidate > now else candidate + timedelta(days=1)


class TaskScheduler:
    """任务调度器"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self._running = False
        # 「本跳什么都没做」的限流状态，见 _log_publish_tick。
        self._last_publish_skip_reason: Optional[str] = None
        self._last_publish_skip_log: Optional[datetime] = None
        self._daily_cycle_lock = threading.Lock()

    def start(self):
        """启动调度器"""
        if self._running:
            return

        self._add_recipe_check_task()
        self._add_expiring_check_task()
        self._add_inventory_check_task()
        self._add_display_publish_task()

        self.scheduler.start()
        self._running = True
        # apscheduler 自己每次执行都打两行 INFO（Running job / executed successfully）。
        # 每日发布检查每 5 分钟一跳，一天就是 576 行纯框架噪音，而它们不含任何业务信息 ——
        # 真机排障时正是这些行让人误以为"一直在下发画面"，实际上业务侧什么都没做。
        # 保留 WARNING 及以上（任务抛异常、错过执行时间那类才是要看的）。
        logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
        logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
        logger.info('定时任务调度器已启动：每日菜单轮换/过期检查/库存检查/每日整套下发'
                    '（检查每 %d 分钟一跳，自动下发=%s，每日更新时间=%s）',
                    settings_store.DISPLAY_PUBLISH_TICK_MINUTES,
                    '开' if settings_store.auto_publish_enabled() else '关',
                    settings_store.daily_publish_time())

    def stop(self):
        """停止调度器"""
        if self._running:
            self.scheduler.shutdown()
            self._running = False
            logger.info('定时任务调度器已停止')

    def is_running(self) -> bool:
        """是否运行中"""
        return self._running

    def get_tasks(self) -> List[Dict]:
        """获取所有任务"""
        tasks = []
        for job in self.scheduler.get_jobs():
            tasks.append({
                'id': job.id,
                'name': job.name,
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })
        return tasks

    def get_task_history(self, limit: int = 100) -> List[Dict]:
        """取最近的任务执行历史，按时间正序（最新在尾）。"""
        return db.get_task_records(limit)

    # ==================== 定时任务配置 ====================

    def _add_recipe_check_task(self):
        """添加每日菜单轮换检查任务。"""
        self.scheduler.add_job(
            func=self._rotate_daily_menu_if_due,
            trigger=IntervalTrigger(minutes=10),
            id='daily_menu_rotation',
            name='每日菜单轮换',
            replace_existing=True
        )

    def _add_expiring_check_task(self):
        """添加过期检查任务"""
        # 每天早上 8 点检查
        self.scheduler.add_job(
            func=self._check_expiring,
            trigger=CronTrigger(hour=8, minute=0),
            id='expiring_check',
            name='过期食材检查',
            replace_existing=True
        )

    def _add_inventory_check_task(self):
        """添加库存检查任务"""
        # 每天中午 12 点检查
        self.scheduler.add_job(
            func=self._check_inventory,
            trigger=CronTrigger(hour=12, minute=0),
            id='inventory_check',
            name='库存检查',
            replace_existing=True
        )

    def _add_display_publish_task(self):
        """添加每日整套画面下发任务。"""
        # 注意这里【无条件注册】，开关判定放在每一跳里（见 _publish_meal_display）。
        #
        # 以前是「开关关着就不注册 job」。自从这个开关可以在 App 里改
        # （settings_store.auto_publish_enabled()，DB 优先），那种写法就成了 bug：
        # 启动时是关的 → job 不存在 → 用户在 App 里打开 → 接口返回 200、GET 也显示
        # 已开启 → 但没有任何 job 会跑，必须重启后端才生效。一个「改了没反应、
        # 还告诉你改成功了」的开关比没有开关更糟。
        #
        # 代价只是关闭状态下每 5 分钟一次空转（一次 settings_store 读取即返回）。
        if not settings_store.auto_publish_enabled():
            logger.info('自动下发当前为关闭状态，任务已注册但每跳会直接跳过'
                        '（可在 App 里打开，无需重启）')

        # 保留短周期检查以容忍进程重启和临时失败；24 小时幂等保证每天只真发一次。
        self.scheduler.add_job(
            func=self._publish_meal_display,
            trigger=IntervalTrigger(minutes=settings_store.DISPLAY_PUBLISH_TICK_MINUTES),
            id='display_publish',
            name='每日整套画面下发',
            replace_existing=True
        )

    # ==================== 任务执行逻辑 ====================

    def _publish_meal_display(self):
        """每日更新时间后发布一整套四张；当天成功过即按 24 小时幂等跳过。"""
        if not settings_store.auto_publish_enabled():
            self._log_publish_tick('自动下发已关闭，本跳跳过')
            return

        now = datetime.now()
        publish_time = settings_store.daily_publish_time()
        hour, minute = map(int, publish_time.split(':'))
        if (now.hour, now.minute) < (hour, minute):
            self._log_publish_tick(f'尚未到每日更新时间 {publish_time}')
            return

        if not self._rotate_daily_menu_if_due(now):
            self._log_publish_tick('每日菜单尚未生成成功，本跳稍后重试')
            return
        key = next((meal for meal in meal_keys_ordered()
                    if self._stored_plan_for_meal(meal)), 'breakfast')
        try:
            self._publish_one_meal(
                key, source='scheduler:daily', idempotency_hours=24
            )
        except Exception as exc:
            self._record_task('display_publish', '每日整套画面下发', False, str(exc))
            logger.exception('每日整套画面下发失败: %s', exc)

    def _rotate_daily_menu_if_due(self, now: datetime = None) -> bool:
        """每日更新时间后生成三餐；全部成功才清除库存 dirty 标记。"""
        now = now or datetime.now()
        publish_time = settings_store.daily_publish_time()
        hour, minute = map(int, publish_time.split(':'))
        if (now.hour, now.minute) < (hour, minute):
            return False

        with self._daily_cycle_lock:
            state = db.get_daily_plan_state()
            today = now.date().isoformat()
            if state['last_generated_date'] == today and not state['dirty']:
                return True

            members = db.get_all_members()
            people_count = len(members) if members else 3
            reason = '库存已变化' if state['dirty'] else f'每日轮换 {today}'
            revision = state.get('revision', 0)
            completed = db.get_daily_plan_progress(today, revision)
            all_ok = True

            for key in meal_keys_ordered():
                if key in completed:
                    continue
                label = meal_label(key)
                try:
                    result = generate_recommendation(
                        label,
                        people_count,
                        user_input=f'{reason}，请与上一日菜单保持变化',
                    )
                except Exception as exc:
                    all_ok = False
                    logger.exception('每日菜单轮换生成%s失败: %s', label, exc)
                    continue

                if result.get('action') != 'recommend':
                    all_ok = False
                    logger.warning('每日菜单轮换生成%s未成功: action=%s reply=%r',
                                   label, result.get('action'),
                                   str(result.get('reply') or '')[:120])
                    continue

                if not db.mark_daily_plan_meal_completed(today, revision, key):
                    self._record_task(
                        'daily_menu_rotation', '生成每日三餐菜单', False,
                        '生成期间库存已变化，将按新库存重试'
                    )
                    return False
                add_recipe_notification(
                    meal_type=label,
                    message=result.get('reply', ''),
                    data=result.get('data', {}),
                )

            if not all_ok:
                self._record_task('daily_menu_rotation', '生成每日三餐菜单', False,
                                  '至少一个餐次生成失败，将在下一跳重试')
                return False

            if not db.mark_daily_plan_generated(today, revision):
                self._record_task(
                    'daily_menu_rotation', '生成每日三餐菜单', False,
                    '生成完成前库存已变化，将按新库存重试'
                )
                return False
            self._record_task('daily_menu_rotation', '生成每日三餐菜单', True)
            logger.info('每日三餐菜单轮换完成: date=%s reason=%s people=%d',
                        today, reason, people_count)
            return True

    def _log_publish_tick(self, reason: str, always: bool = False) -> None:
        """记一条「本跳什么都没做」的原因，默认每小时最多一条。

        为什么要限流：这个任务每 5 分钟一跳，一天 288 次。每跳都打 INFO 会把日志刷成一片
        重复行，真正要看的东西被挤走；而完全不打就回到了「看不出它在干什么」。折中是
        **原因变化时立刻打、原因不变时每小时一条** —— 既能确认它活着且在做判断，又不淹没日志。

        `always=True` 用于本身就该被注意的原因（例如时间表为空 = 永远不会发图）。
        """
        now = datetime.now()
        changed = reason != self._last_publish_skip_reason
        due = (self._last_publish_skip_log is None
               or (now - self._last_publish_skip_log).total_seconds() >= 3600)
        if always or changed or due:
            if settings_store.auto_publish_enabled():
                next_at = next_daily_publish_at(now, settings_store.daily_publish_time())
                next_text = next_at.strftime('%Y-%m-%d %H:%M') if next_at else '每日更新时间无效'
            else:
                next_text = '自动下发关闭'
            logger.info('每日下发检查：%s；下一次预计下发=%s', reason, next_text)
            self._last_publish_skip_log = now
        self._last_publish_skip_reason = reason

    def _publish_one_meal(self, key: str, force: bool = False,
                          device_id: str = None, source: str = None,
                          idempotency_hours: int = 6) -> List[Dict]:
        """渲染并发布一整套画面（总览 + 早/中/晚详情），返回**本次**产生的执行记录。

        返回记录而不是让调用方去翻共享历史：那份历史被 4 个调度 job、8 个请求线程与
        设备按键线程共写，按长度做游标再切片会把别人插的记录算进本次结果。
        空列表即表示幂等跳过。

        触发餐段必须有菜谱方案，否则整套都不发：那一餐的详情页是这次发布的主角，
        主角缺席时宁可让屏幕留着上一张 —— 电子纸不会自己恢复，一张不该出现的画面
        会一直挂到下一次成功发布。

        手动按餐次发布时，其余两餐只取库里**已有**的方案，不会为了凑齐四张而额外打模型。
        每日任务会在进入这里前生成齐三餐，任一餐失败都不会发布。

        Args:
            force: True 时跳过「近期已下发过」的幂等检查，其余逻辑完全一致。
                默认 False —— 定时任务与既有调用方共用这个函数，它们要的正是幂等。
        """
        records: List[Dict] = []
        label = meal_label(key)
        source = source or f'scheduler:{key}'
        targets = [device_id] if device_id else self._publish_targets()
        if not targets:
            # 一台设备都没有时不要每 5 分钟刷一条失败记录，只在窗口内记一次。
            if not self._recent_task('display_publish', '无可下发设备'):
                records.append(self._record_task(
                    'display_publish', '无可下发设备', False,
                    '设备表为空，且未配置 SFM_AUTO_PUBLISH_DEVICES'))
            return records

        # 幂等窗口约束轮询式自动任务，不该拦住**人的显式操作**。用户在对话里点
        # 「用这份菜单」时已经明确表达了
        # 「我要这一份」，此时返回「近期已下发过」等于屏幕不动、系统什么都不做，
        # 而接口还是 200。所以人按的按钮走 force=True。
        render_preferences = {}
        for target in targets:
            layout = menu_render.get_layout(
                device_display.get_device_layout(target)).key
            options = menu_render.normalize_layout_options(
                device_display.get_device_layout_options(target))
            render_preferences[target] = (
                layout,
                options,
                layout_publish_source(source, layout, options),
            )

        pending = targets if force else [
            d for d in targets
            if not device_display.has_recent_upload(
                d, render_preferences[d][2], within_hours=idempotency_hours)
        ]
        if not pending:
            # 说清楚是幂等跳过而不是失败，避免把正常去重误判成没有进入发布窗口。
            logger.info('%s画面近期已下发过，本跳跳过（幂等窗口内）：devices=%s',
                        label, targets)
            return records

        plan = self._plan_for_meal(key)
        if plan is None:
            records.append(self._record_task(
                'display_publish', f'下发{label}整套画面', False,
                '当天没有可用菜谱方案，且重新生成失败'))
            return records

        # 当天三餐各自的方案：触发餐段用上面拿到的那份，另外两餐只读库里已有的。
        plans = {
            meal: plan if meal == key else self._stored_plan_for_meal(meal)
            for meal in meal_keys_ordered()
        }

        # 总览汇总当天**各餐**的菜品，而不是只放触发的这一餐：总览是设备大部分时间
        # 显示的那张，站在冰箱前的人想知道的是「今天吃什么」，不是「下一顿吃什么」。
        # 没有方案的餐段直接不出现，留白比印一行「暂无」更干净。
        dishes = []
        for meal in meal_keys_ordered():
            meal_plan = plans.get(meal)
            if not meal_plan:
                continue
            prefix = meal_label(meal)
            dishes.extend(f'{prefix}|{line}'
                          for line in menu_render.dishes_from_plan(meal_plan))
        if not dishes:
            records.append(self._record_task(
                'display_publish', f'下发{label}整套画面', False, '方案里没有菜品'))
            return records

        expiring_items = db.get_expiring_items(days=3) or []
        expiring = '、'.join(item['name'] for item in expiring_items[:4])
        if expiring:
            expiring += ' 即将到期'

        now = datetime.now()
        date_text = f'{now.month}月{now.day}日'

        # 版式是**按设备**的偏好，但相同版式的四张图完全一样。一套四张约 1.6 秒 CPU，
        # 按设备逐台渲染在多设备下是纯粹的重复计算，故按 layout 缓存渲染结果。
        rendered: Dict[tuple, List[Dict]] = {}

        for device_id in pending:
            layout, layout_options, publish_source = render_preferences[device_id]
            cache_key = (layout, tuple(sorted(layout_options.items())))
            items = rendered.get(cache_key)
            if items is None:
                items = self._render_film_set(
                    layout, layout_options, date_text, dishes, expiring, plans)
                rendered[cache_key] = items

            result = device_display.upload_film_set(
                device_id, items, uploaded_by=publish_source)
            if result.get('success'):
                records.append(self._record_task(
                    'display_publish',
                    f"下发{label}整套画面（{len(items)} 张 / {layout}）"
                    f" -> {device_id} v{result['version']}",
                    True))
                logger.info('已发布%s整套画面: device=%s layout=%s version=%s slots=%s',
                            label, device_id, layout, result['version'],
                            [item['slot'] for item in result.get('items', [])])
            else:
                records.append(self._record_task(
                    'display_publish', f'下发{label}整套画面 -> {device_id}',
                    False, result.get('error', '')))
                logger.error('发布%s整套画面失败: device=%s %s',
                             label, device_id, result.get('error'))

        return records

    def _render_film_set(self, layout: str, layout_options: dict, date_text: str,
                         dishes: List[str], expiring: str,
                         plans: Dict[str, Optional[Dict]]) -> List[Dict]:
        """渲染一整套四张画面，返回 upload_film_set() 要的 items。

        没有方案的餐段**也要出一张占位详情页**，不能少发。原因在文件名上：四张图的文件名
        是固定的跨端契约（menu_render.SLOT_FILENAMES），少发一张时设备 SD 卡上留着的是
        上一版那张同名详情页，到了那个时段固件会照常把它显示出来，而人看不出这是隔夜菜单。
        """
        # 分栏版式的栏头时间与菜名下的用料摘要。无条件算：让渲染层决定要不要，
        # 而不是在这里按版式分叉。
        meal_times = {}
        for row in db.get_meal_schedule() or []:
            time_text = (row.get('meal_time') or '').strip()
            if time_text:
                meal_times[meal_label(row.get('meal_type'))] = time_text
        details = {}
        for meal_plan in plans.values():
            if meal_plan:
                details.update(menu_render.ingredient_summaries(meal_plan))

        items = [{
            'slot': 'today',
            'data': menu_render.render_overview_film(
                title='今日菜单',
                date_text=date_text,
                dishes=dishes,
                expiring=expiring,
                layout=layout,
                layout_options=layout_options,
                meal_times=meal_times,
                details=details,
            ),
        }]

        for meal in meal_keys_ordered():
            meal_label_text = meal_label(meal)
            dish_list = (plans.get(meal) or {}).get('dishes') or []
            if dish_list:
                lines = menu_render.detail_lines_from_dish(dish_list[0])
            else:
                # 说明拆成两行，不是排版洁癖：渲染不会自动折行，超出画布的部分被直接
                # 截掉且不报错。整句在大字版式（可用宽度 736px，需 764px）和竖版
                # （可用 472px）下都装不下，屏幕上会看到一句没有结尾的话。
                lines = [
                    f'今日暂无{meal_label_text}菜单',
                    '可在 App 里生成',
                    '或等下一个每日更新时间',
                ]
            items.append({
                'slot': meal,
                'data': menu_render.render_meal_detail_film(
                    meal, lines, layout=layout, layout_options=layout_options),
            })

        return items

    def _publish_targets(self) -> List[str]:
        """自动下发的目标设备列表。

        显式白名单优先；留空时取所有已注册设备 —— 设备是在首次上传/上报时自动登记的，
        所以「已注册」等价于「这套后端见过的设备」，对一户一台的场景就是正确答案。
        """
        configured = config.auto_publish_devices()
        if configured:
            return configured
        return [d['device_id'] for d in device_display.list_devices() if d.get('device_id')]

    def _stored_plan_for_meal(self, key: str) -> Optional[Dict]:
        """取当天该餐**已落库**的菜谱方案；没有就返回 None，绝不调用模型。

        与 _plan_for_meal() 分开，是因为手动按餐次发布只应为用户指定的那餐打模型；
        每日任务会在发布前通过独立轮换流程生成齐三餐。
        """
        recent = _utc_window_start(12)

        for plan in db.get_pending_plans():     # 已按 created_at DESC 排序
            if meal_key(plan.get('meal_type')) != key:
                continue
            if (plan.get('created_at') or '') < recent:
                continue    # 隔夜的旧方案：那些食材大概已经吃掉了
            chosen = plan.get('selected_plan')
            data = plan.get('plan_b') if chosen == 'b' else plan.get('plan_a')
            if data:
                return data
        return None

    def _plan_for_meal(self, key: str) -> Optional[Dict]:
        """取当天该餐可用的菜谱方案；没有就现生成一份。

        优先复用已有方案，是为了让「设备上显示的」与「App 里看到的、通知里推过的」
        是同一份 —— 两处各生成一次，模型输出必然不同，用户会以为自己看错了。
        """
        label = meal_label(key)

        stored = self._stored_plan_for_meal(key)
        if stored:
            return stored

        # 没有现成方案（例如推荐任务那一跳恰好失败），补生成一次
        members = db.get_all_members()
        result = generate_recommendation(label, len(members) if members else 3)
        if result.get('action') != 'recommend':
            # inventory_empty / recommend_error：属于预期内的「这次没得发」
            logger.info('%s没有可用菜谱: %s', label, result.get('action'))
            return None
        data = result.get('data') or {}
        return data.get('plan_a') or data.get('plan_b')

    def _check_and_recommend(self):
        """
        检查是否需要推荐菜谱

        逻辑：
        1. 获取用餐时间配置
        2. 检查当前时间是否在用餐前1-2小时
        3. 如果是，生成推荐并发送通知
        """
        schedule = db.get_meal_schedule()
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        # 与餐前下发同一个思路：这个任务每 10 分钟一跳，绝大多数跳什么都不做。
        # 不打日志就分不清「还没到窗口」「已经推荐过了」「生成失败了」三种情况，
        # 而它们的下一步动作完全不同。
        skipped = []

        for meal in schedule:
            # 库里存的是 breakfast/lunch/dinner，直接送进 prompt 会变成「餐次：breakfast」，
            # 模型答出来的东西会跟着跑偏。这里统一换成中文标签。
            meal_type = meal_label(meal_key(meal['meal_type']) or '') or meal['meal_type']
            meal_time_str = meal['meal_time']

            try:
                meal_hour, meal_minute = map(int, meal_time_str.split(':'))
                meal_total_minutes = meal_hour * 60 + meal_minute
                current_total_minutes = current_hour * 60 + current_minute

                diff = meal_total_minutes - current_total_minutes

                # 如果在用餐前60-120分钟之间
                if 60 <= diff <= 120:
                    if not self._has_recent_recommendation(meal_type):
                        # 获取家庭成员数量作为默认人数
                        members = db.get_all_members()
                        people_count = len(members) if members else 3

                        logger.info('命中%s推荐窗口（距用餐 %d 分钟），开始生成：人数=%d',
                                    meal_type, diff, people_count)

                        # 生成推荐
                        result = generate_recommendation(meal_type, people_count)

                        # 发送通知
                        add_recipe_notification(
                            meal_type=meal_type,
                            message=result.get('reply', ''),
                            data=result.get('data', {})
                        )

                        self._record_task('recipe_check', f'生成{meal_type}推荐', True)

                        # action 不是 recommend 时说明生成没成功（库存为空 / 模型失败 /
                        # 过敏拦截），必须记下来 —— 否则「定时推荐没生成」在日志里
                        # 与「压根没进窗口」一样看不出区别，而 agent 那边只把原因写进了
                        # 给用户看的 reply 文本里。
                        action = result.get('action')
                        if action == 'recommend':
                            logger.info('已生成%s推荐：plan_id=%s',
                                        meal_type, (result.get('data') or {}).get('plan_id'))
                        else:
                            logger.warning('生成%s推荐未成功：action=%s reply=%r',
                                           meal_type, action,
                                           str(result.get('reply') or '')[:120])
                    else:
                        skipped.append(f'{meal_type}(近期已推荐过)')
                else:
                    skipped.append(f'{meal_type}(距用餐{diff}分钟，窗口是60~120)')

            except Exception as e:
                self._record_task('recipe_check', f'检查{meal_type}', False, str(e))
                logger.error('检查%s推荐窗口时出错：%s', meal_type, e)

        if skipped and len(skipped) == len(schedule):
            # 全部跳过时打一条 DEBUG：这是绝大多数情况，用 INFO 会每 10 分钟刷一行。
            # 需要看的时候把 SFM_LOG_LEVEL 调成 DEBUG 即可。
            logger.debug('菜谱推荐检查：本跳无动作 —— %s', '、'.join(skipped))
        elif not schedule:
            logger.warning('菜谱推荐检查：用餐时间表为空，定时推荐永远不会触发')

    def _check_expiring(self):
        """
        检查即将过期的食材

        逻辑：
        1. 查询3天内过期的食材
        2. 如果有，发送通知
        """
        try:
            expiring = db.get_expiring_items(days=3)

            if expiring:
                add_expiring_notification(expiring)
                self._record_task('expiring_check', f'发现{len(expiring)}种即将过期食材', True)
                logger.info('发现 %d 种食材即将过期', len(expiring))
            else:
                self._record_task('expiring_check', '没有即将过期食材', True)

        except Exception as e:
            self._record_task('expiring_check', '检查过期食材', False, str(e))
            logger.exception('检查过期食材时出错：%s', e)

    def _check_inventory(self):
        """
        检查库存情况

        逻辑：
        1. 按分类统计库存
        2. 如果某分类库存不足，发送通知
        """
        try:
            categories = db.get_inventory_categories()

            # 定义最低库存阈值
            thresholds = {
                '蔬菜': 2,
                '水果': 2,
                '肉类': 1,
                '蛋类': 1,
                '乳制品': 1,
                '主食': 1
            }

            for cat, threshold in thresholds.items():
                # 查找该分类的库存数量
                cat_data = next((c for c in categories if c['name'] == cat), None)
                count = cat_data['count'] if cat_data else 0

                if count < threshold:
                    add_inventory_low_notification(cat, count)
                    self._record_task('inventory_check', f'{cat}库存不足({count})', True)
                    logger.info('%s库存不足：%d种', cat, count)

        except Exception as e:
            self._record_task('inventory_check', '检查库存', False, str(e))
            logger.exception('检查库存时出错：%s', e)

    # ==================== 辅助方法 ====================

    def _has_recent_recommendation(self, meal_type: str) -> bool:
        """检查是否已有最近的推荐（避免重复生成）。

        两处口径：

        1) 时间用「最近 12 小时」而不是「今天」。meal_plan.created_at 由 SQLite 的
           CURRENT_TIMESTAMP 写入，是 **UTC**，按本地日期比会在 +0800 下错判
           （早餐 07:30 的记录在库里是前一天 23:30），导致每 10 分钟重新生成一次。
        2) 餐次用归一后的键比较：库里存英文键，调用方传的是中文标签，裸 `==` 不成立。
        """
        target = meal_key(meal_type)
        recent = _utc_window_start(12)

        for plan in db.get_pending_plans():
            if (plan.get('created_at') or '') < recent:
                continue
            if meal_key(plan.get('meal_type')) == target:
                return True

        return False

    def _recent_task(self, task_id: str, description: str, within_minutes: int = 60) -> bool:
        """最近是否已有同一条执行记录，用于压制重复的失败噪声。"""
        return db.has_recent_task_record(task_id, description, within_minutes)

    def _record_task(self, task_id: str, description: str, success: bool,
                     error: str = None) -> Dict:
        """记录一次任务执行结果，返回落库的那条记录。

        落库而不是留在内存里：容器一重启（部署、OOM、平台迁移）历史就归零，而它正是
        排查「昨天那次自动下发到底发生了没有」的唯一线索 —— 那种问题只会在事后被问起。
        """
        return db.add_task_record(task_id, description, success, error)


# ==================== 全局实例 ====================

task_scheduler = TaskScheduler()


# ==================== 便捷函数 ====================

def init_scheduler():
    """初始化调度器"""
    task_scheduler.start()


def stop_scheduler():
    """停止调度器"""
    task_scheduler.stop()


def is_scheduler_running() -> bool:
    """调度器是否运行中"""
    return task_scheduler.is_running()


def get_scheduler_tasks() -> List[Dict]:
    """获取所有任务"""
    return task_scheduler.get_tasks()


def get_task_history() -> List[Dict]:
    """获取任务历史"""
    return task_scheduler.get_task_history()


def trigger_recommendation(meal_type: str = None, people_count: int = None) -> Dict:
    """
    手动触发推荐

    Args:
        meal_type: 餐次（默认根据当前时间判断）
        people_count: 人数（默认家庭成员数）

    Returns:
        dict: 推荐结果
    """
    if not meal_type:
        hour = datetime.now().hour
        if hour < 10:
            meal_type = '早餐'
        elif hour < 14:
            meal_type = '午餐'
        else:
            meal_type = '晚餐'

    if not people_count:
        members = db.get_all_members()
        people_count = len(members) if members else 3

    result = generate_recommendation(meal_type, people_count)

    # 发送通知
    add_recipe_notification(
        meal_type=meal_type,
        message=result.get('reply', ''),
        data=result.get('data', {})
    )

    return result


def trigger_display_publish(meal_type: str = None, force: bool = False,
                            device_id: str = None) -> Dict:
    """手动触发一次整套画面下发（不等每日窗口）。

    Args:
        meal_type: 餐次，中英文皆可（breakfast/早餐...）。留空按当前时间推断。
        force: True 时无视兼容路径的幂等窗口，照发。用于**人的显式操作**（对话里点
            「用这份菜单」、控制台点「立即重新生成并下发」）。默认 False：既有调用方与
            定时任务共用这条路径，它们要的是幂等。
        device_id: 非空时只发布到这一台设备。设备物理按键入口必须传入，不能让一台
            设备的操作改动同一后端下的其它屏幕。

    Returns:
        {"success": bool, "meal": "lunch", "devices": [...], "error": "..."}
    """
    key = meal_key(meal_type) if meal_type else None
    if key is None:
        if meal_type:
            return {
                "success": False,
                "meal": meal_type,
                "devices": [],
                "error": f'无法识别餐次 {meal_type!r}（可选 breakfast/lunch/dinner 或早餐/午餐/晚餐）'
            }
        hour = datetime.now().hour
        key = 'breakfast' if hour < 10 else ('lunch' if hour < 14 else 'dinner')

    targets = [device_id] if device_id else task_scheduler._publish_targets()
    try:
        # 手动触发要能看出「到底发了没有」：只看返回 200 会把「已发过、这次跳过」
        # 误读成成功下发。判据取自本次调用的返回值，而不是共享的执行历史 ——
        # 那份历史被多个线程共写，用下标去切「本次」的部分会串台。
        records = task_scheduler._publish_one_meal(key, force=force, device_id=device_id)
    except Exception as e:
        logger.error('手动下发%s画面失败: %s', meal_label(key), e)
        return {"success": False, "meal": key, "devices": targets, "error": str(e)}

    failed = [r for r in records if not r['success']]
    if failed:
        return {"success": False, "meal": key, "devices": targets,
                "error": failed[-1].get('error') or failed[-1]['description']}
    if not records:
        return {"success": True, "meal": key, "devices": targets,
                "skipped": True, "detail": '该餐次近期已下发过，未重复发布'}
    return {"success": True, "meal": key, "devices": targets,
            "published": [r['description'] for r in records]}


def trigger_expiring_check() -> Dict:
    """
    手动触发过期检查

    Returns:
        dict: 检查结果
    """
    expiring = db.get_expiring_items(days=3)

    if expiring:
        add_expiring_notification(expiring)

    return {
        'expiring_count': len(expiring),
        'items': expiring
    }


def trigger_inventory_check() -> Dict:
    """
    手动触发库存检查

    Returns:
        dict: 检查结果
    """
    categories = db.get_inventory_categories()
    low_inventory = []

    thresholds = {
        '蔬菜': 2,
        '水果': 2,
        '肉类': 1,
        '蛋类': 1,
        '乳制品': 1,
        '主食': 1
    }

    for cat, threshold in thresholds.items():
        cat_data = next((c for c in categories if c['name'] == cat), None)
        count = cat_data['count'] if cat_data else 0

        if count < threshold:
            low_inventory.append({'category': cat, 'count': count, 'threshold': threshold})
            add_inventory_low_notification(cat, count)

    return {
        'low_inventory_count': len(low_inventory),
        'low_inventory': low_inventory
    }


# ==================== 测试 ====================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("TaskScheduler Module Test")
    print("=" * 50)

    # 测试启动
    init_scheduler()
    print(f"Running: {is_scheduler_running()}")

    # 测试获取任务
    tasks = get_scheduler_tasks()
    print(f"\nTasks ({len(tasks)}):")
    for task in tasks:
        print(f"  - {task['name']} ({task['id']})")

    # 测试手动触发
    print("\nManual trigger: expiring check")
    result = trigger_expiring_check()
    print(f"  Expiring: {result['expiring_count']} items")

    # 测试任务历史
    history = get_task_history()
    print(f"\nTask history: {len(history)} records")

    # 停止
    stop_scheduler()
    print(f"\nRunning: {is_scheduler_running()}")

    print("\nDone!")
