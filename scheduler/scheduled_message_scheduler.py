import asyncio
import logging
import traceback
from datetime import datetime, timedelta

import pytz
from telethon import errors, TelegramClient
from sqlalchemy.orm import joinedload

from models.models import ForwardRule, ScheduledMessageConfig, get_session
from utils.constants import DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)


class ScheduledMessageScheduler:
    def __init__(self, bot_client: TelegramClient):
        self.bot_client = bot_client
        self.timezone = pytz.timezone(DEFAULT_TIMEZONE)
        self.tasks = {}
        self.reconcile_task = None

    async def start(self):
        """启动所有定时发布任务"""
        logger.info("开始启动定时发布调度器...")
        session = get_session()
        try:
            configs = session.query(ScheduledMessageConfig).all()
            for config in configs:
                await self.schedule_config(config.id)
            self.reconcile_task = asyncio.create_task(self._reconcile_loop())
            logger.info(f"定时发布调度器启动完成，共加载 {len(configs)} 个配置")
        except Exception as exc:
            logger.error(f"启动定时发布调度器失败: {exc}")
            logger.error(traceback.format_exc())
        finally:
            session.close()

    def stop(self):
        """停止所有定时任务"""
        if self.reconcile_task:
            self.reconcile_task.cancel()
            self.reconcile_task = None
        for task in self.tasks.values():
            task.cancel()
        self.tasks.clear()

    async def _reconcile_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                await self.reconcile()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error(f"定时发布调度器对账失败: {exc}")
                logger.error(traceback.format_exc())

    async def reconcile(self):
        """定时从数据库重新对账，兼容 Web 管理页直接写库"""
        session = get_session()
        try:
            configs = session.query(ScheduledMessageConfig).all()
            db_ids = {config.id for config in configs}
            task_ids = set(self.tasks.keys())

            for config_id in task_ids - db_ids:
                task = self.tasks.pop(config_id, None)
                if task:
                    task.cancel()

            for config in configs:
                if config.enabled and config.rule_id:
                    if config.id not in self.tasks or self.tasks[config.id].done():
                        await self.schedule_config(config.id)
                elif config.id in self.tasks:
                    task = self.tasks.pop(config.id, None)
                    if task:
                        task.cancel()
        finally:
            session.close()

    async def schedule_config(self, config_id: int):
        """创建或刷新单个配置的调度任务"""
        if config_id in self.tasks:
            self.tasks[config_id].cancel()
            del self.tasks[config_id]

        session = get_session()
        try:
            config = self._get_config(session, config_id)
            if not config or not config.enabled or not config.rule or not config.rule.enable_rule:
                return

            task = asyncio.create_task(self._run_config_task(config_id))
            self.tasks[config_id] = task
        finally:
            session.close()

    async def refresh_rule(self, rule_id: int):
        """刷新某条规则下的所有定时发布任务"""
        current_task_ids = [
            config_id for config_id, task in self.tasks.items()
            if not task.done() and self._task_belongs_to_rule(config_id, rule_id)
        ]
        for config_id in current_task_ids:
            task = self.tasks.pop(config_id, None)
            if task:
                task.cancel()

        session = get_session()
        try:
            config_ids = [
                config.id
                for config in session.query(ScheduledMessageConfig).filter(
                    ScheduledMessageConfig.rule_id == rule_id
                ).all()
            ]
        finally:
            session.close()

        for config_id in config_ids:
            await self.schedule_config(config_id)

    async def delete_config(self, config_id: int):
        task = self.tasks.pop(config_id, None)
        if task:
            task.cancel()

    def _task_belongs_to_rule(self, config_id: int, rule_id: int) -> bool:
        session = get_session()
        try:
            config = session.query(ScheduledMessageConfig).get(config_id)
            return bool(config and config.rule_id == rule_id)
        finally:
            session.close()

    def _get_config(self, session, config_id: int):
        return session.query(ScheduledMessageConfig).options(
            joinedload(ScheduledMessageConfig.rule).joinedload(ForwardRule.target_chat)
        ).filter(ScheduledMessageConfig.id == config_id).first()

    def _parse_datetime(self, value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return self.timezone.localize(dt)
            return dt.astimezone(self.timezone)
        except ValueError:
            return None

    def _format_datetime(self, value: datetime):
        return value.astimezone(self.timezone).isoformat()

    def _get_interval_delta(self, config: ScheduledMessageConfig):
        interval = max(int(config.interval_value or 1), 1)
        if config.schedule_type == 'interval_hours':
            return timedelta(hours=interval)
        return timedelta(minutes=interval)

    def _calculate_next_run(self, config: ScheduledMessageConfig, now: datetime):
        if config.schedule_type == 'daily':
            try:
                hour, minute = map(int, (config.daily_time or '09:00').split(':'))
            except ValueError:
                hour, minute = 9, 0

            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            return next_run

        stored_next_run = self._parse_datetime(config.next_run_at)
        if stored_next_run and stored_next_run > now:
            return stored_next_run

        return now + self._get_interval_delta(config)

    def _calculate_following_run(self, config: ScheduledMessageConfig, current_run: datetime):
        if config.schedule_type == 'daily':
            return current_run + timedelta(days=1)
        return current_run + self._get_interval_delta(config)

    async def _run_config_task(self, config_id: int):
        while True:
            try:
                session = get_session()
                try:
                    config = self._get_config(session, config_id)
                    if not config or not config.enabled or not config.rule or not config.rule.enable_rule:
                        self.tasks.pop(config_id, None)
                        return

                    now = datetime.now(self.timezone)
                    next_run = self._calculate_next_run(config, now)
                    if config.next_run_at != self._format_datetime(next_run):
                        config.next_run_at = self._format_datetime(next_run)
                        session.commit()

                    wait_seconds = max((next_run - now).total_seconds(), 0)
                finally:
                    session.close()

                await asyncio.sleep(wait_seconds)
                await self._execute_config(config_id)
            except asyncio.CancelledError:
                logger.info(f"定时发布配置 {config_id} 任务已取消")
                return
            except Exception as exc:
                logger.error(f"执行定时发布配置 {config_id} 任务时出错: {exc}")
                logger.error(traceback.format_exc())
                await asyncio.sleep(60)

    async def _execute_config(self, config_id: int):
        session = get_session()
        try:
            config = self._get_config(session, config_id)
            if not config or not config.enabled or not config.rule or not config.rule.enable_rule:
                return

            target_chat = config.rule.target_chat
            if not target_chat:
                logger.warning(f"定时发布配置 {config_id} 缺少目标聊天，跳过发送")
                return

            current_run = self._parse_datetime(config.next_run_at) or datetime.now(self.timezone)
            parse_mode = None
            if config.rule.message_mode:
                parse_mode = config.rule.message_mode.value.lower()

            try:
                await self.bot_client.send_message(
                    int(target_chat.telegram_chat_id),
                    config.message_text,
                    parse_mode=parse_mode
                )
            except errors.MarkupInvalidError:
                await self.bot_client.send_message(
                    int(target_chat.telegram_chat_id),
                    config.message_text
                )

            now = datetime.now(self.timezone)
            next_run = self._calculate_following_run(config, current_run if current_run > now - timedelta(minutes=1) else now)
            config.last_sent_at = self._format_datetime(now)
            config.next_run_at = self._format_datetime(next_run)
            session.commit()

            logger.info(
                "定时发布成功: config_id=%s rule_id=%s next_run=%s",
                config.id,
                config.rule_id,
                config.next_run_at,
            )
        except Exception as exc:
            session.rollback()
            logger.error(f"执行定时发布发送时出错: {exc}")
            logger.error(traceback.format_exc())
            raise
        finally:
            session.close()
