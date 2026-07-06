import asyncio
import logging
import os
import traceback

from telethon import Button
from telethon.tl import types

from handlers.button.button_helpers import (
    create_scheduled_config_details_buttons,
    create_scheduled_settings_buttons,
)
from models.models import ForwardRule, RuleSync, ScheduledMessageConfig
from managers.state_manager import state_manager
from utils.common import get_scheduled_message_scheduler, is_admin
from utils.constants import SCHEDULED_MESSAGE_SETTINGS_TEXT

logger = logging.getLogger(__name__)


def parse_scheduled_message_payload(raw_text: str):
    lines = [line.rstrip() for line in raw_text.strip().splitlines()]
    if not lines:
        return False, "请输入配置内容", None

    values = {}
    content_lines = []
    in_content = False

    for line in lines:
        stripped = line.strip()
        if not stripped and not in_content:
            continue
        if stripped.startswith("内容:") or stripped.startswith("内容："):
            in_content = True
            remaining = stripped[3:].lstrip(":：").strip() if len(stripped) > 3 else ""
            if remaining:
                content_lines.append(remaining)
            continue
        if in_content:
            content_lines.append(line)
            continue
        if ":" not in stripped and "：" not in stripped:
            return False, f"无法识别这一行: {stripped}", None

        separator = "：" if "：" in stripped else ":"
        key, value = stripped.split(separator, 1)
        values[key.strip()] = value.strip()

    schedule_type_raw = (values.get("类型") or values.get("type") or "").strip().lower()
    schedule_type_map = {
        "每天": "daily",
        "daily": "daily",
        "每隔小时": "interval_hours",
        "小时": "interval_hours",
        "interval_hours": "interval_hours",
        "每隔分钟": "interval_minutes",
        "分钟": "interval_minutes",
        "interval_minutes": "interval_minutes",
    }
    schedule_type = schedule_type_map.get(schedule_type_raw)
    if not schedule_type:
        return False, "类型仅支持：每天 / 每隔小时 / 每隔分钟", None

    payload = {
        "schedule_type": schedule_type,
        "interval_value": None,
        "daily_time": None,
        "message_text": "\n".join(content_lines).strip(),
        "enabled": True,
    }

    if not payload["message_text"]:
        return False, "消息内容不能为空，请在“内容:”后填写要发送的文本", None

    if schedule_type == "daily":
        daily_time = (values.get("时间") or values.get("time") or "").strip()
        if len(daily_time) != 5 or daily_time[2] != ":":
            return False, "每天模式请填写正确时间，例如 09:30", None
        try:
            hour, minute = map(int, daily_time.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            return False, "每天模式请填写正确时间，例如 09:30", None
        payload["daily_time"] = daily_time
    else:
        interval_raw = (values.get("间隔") or values.get("interval") or "").strip()
        try:
            interval_value = int(interval_raw)
        except ValueError:
            interval_value = 0
        if interval_value <= 0:
            return False, "间隔必须是大于 0 的整数", None
        payload["interval_value"] = interval_value

    return True, "解析成功", payload


def format_scheduled_message_input_text(rule_id, config=None):
    if config:
        if config.schedule_type == "daily":
            schedule_sample = f"类型: 每天\n时间: {config.daily_time or '09:00'}"
        elif config.schedule_type == "interval_hours":
            schedule_sample = f"类型: 每隔小时\n间隔: {config.interval_value or 1}"
        else:
            schedule_sample = f"类型: 每隔分钟\n间隔: {config.interval_value or 30}"
        content_sample = config.message_text or ""
        title = "请发送新的定时发布配置"
        cancel_action = f"cancel_scheduled_message:{config.rule_id}"
    else:
        schedule_sample = "类型: 每天\n时间: 09:00"
        content_sample = "这里填写要自动发布的消息内容"
        title = "请发送定时发布配置"
        cancel_action = f"cancel_scheduled_message:{rule_id}"

    return (
        f"{title}\n"
        f"规则ID: {rule_id}\n\n"
        f"支持格式示例：\n"
        f"{schedule_sample}\n内容:\n{content_sample}\n\n"
        f"其它示例：\n"
        f"类型: 每隔小时\n间隔: 4\n内容:\n每 4 小时自动发送一次\n\n"
        f"类型: 每隔分钟\n间隔: 30\n内容:\n每 30 分钟提醒一次\n\n"
        f"5 分钟内未设置将自动取消",
        [[Button.inline("取消", cancel_action)]],
    )


def scheduled_config_detail_text(config):
    if config.schedule_type == 'daily':
        schedule_desc = f"每天 {config.daily_time or '09:00'}"
    elif config.schedule_type == 'interval_hours':
        schedule_desc = f"每隔 {config.interval_value or 1} 小时"
    else:
        schedule_desc = f"每隔 {config.interval_value or 1} 分钟"

    next_run = config.next_run_at or "未计算"
    last_sent = config.last_sent_at or "尚未发送"

    return (
        f"定时发布配置 #{config.id}\n"
        f"状态: {'启用' if config.enabled else '停用'}\n"
        f"触发方式: {schedule_desc}\n"
        f"下次执行: {next_run}\n"
        f"上次发送: {last_sent}\n\n"
        f"消息内容:\n{config.message_text}"
    )


async def save_scheduled_message_config(session, state, raw_text):
    success, message, payload = parse_scheduled_message_payload(raw_text)
    if not success:
        return False, message, None, []

    affected_rule_ids = set()

    if state.startswith("add_scheduled_message:"):
        rule_id = int(state.split(":")[1])
        rule = session.query(ForwardRule).get(rule_id)
        if not rule:
            return False, "规则不存在", None, []

        config = ScheduledMessageConfig(rule_id=rule_id, **payload)
        session.add(config)
        session.flush()
        affected_rule_ids.add(rule_id)

        if rule.enable_sync:
            sync_rules = session.query(RuleSync).filter(RuleSync.rule_id == rule.id).all()
            for sync_rule in sync_rules:
                session.add(ScheduledMessageConfig(rule_id=sync_rule.sync_rule_id, **payload))
                affected_rule_ids.add(sync_rule.sync_rule_id)

        session.commit()
        return True, "定时发布配置已创建", config.id, list(affected_rule_ids)

    if state.startswith("edit_scheduled_message:"):
        config_id = int(state.split(":")[1])
        config = session.query(ScheduledMessageConfig).get(config_id)
        if not config:
            return False, "配置不存在", None, []

        rule = session.query(ForwardRule).get(config.rule_id)
        old_signature = (
            config.schedule_type,
            config.interval_value,
            config.daily_time,
            config.message_text,
        )
        for key, value in payload.items():
            setattr(config, key, value)
        config.next_run_at = None
        session.flush()
        affected_rule_ids.add(config.rule_id)

        if rule and rule.enable_sync:
            sync_rules = session.query(RuleSync).filter(RuleSync.rule_id == rule.id).all()
            for sync_rule in sync_rules:
                target_config = session.query(ScheduledMessageConfig).filter_by(
                    rule_id=sync_rule.sync_rule_id,
                    schedule_type=old_signature[0],
                    interval_value=old_signature[1],
                    daily_time=old_signature[2],
                    message_text=old_signature[3],
                ).first()
                if target_config:
                    for key, value in payload.items():
                        setattr(target_config, key, value)
                    target_config.next_run_at = None
                    affected_rule_ids.add(sync_rule.sync_rule_id)

        session.commit()
        return True, "定时发布配置已更新", config.id, list(affected_rule_ids)

    return False, "未知的配置状态", None, []


async def refresh_scheduled_rules(rule_ids):
    scheduler = await get_scheduled_message_scheduler()
    if not scheduler:
        return
    for rule_id in sorted(set(rule_ids)):
        await scheduler.refresh_rule(rule_id)


async def callback_scheduled_settings(event, rule_id, session, message, data):
    await event.edit(
        SCHEDULED_MESSAGE_SETTINGS_TEXT,
        buttons=await create_scheduled_settings_buttons(rule_id=rule_id),
        link_preview=False
    )


async def callback_add_scheduled_message(event, rule_id, session, message, data):
    rule = session.query(ForwardRule).get(int(rule_id))
    if not rule:
        await event.answer("规则不存在")
        return

    if isinstance(event.chat, types.Channel):
        if not await is_admin(event):
            await event.answer("只有管理员可以修改设置")
            return
        user_id = os.getenv('USER_ID')
    else:
        user_id = event.sender_id

    chat_id = abs(event.chat_id)
    state = f"add_scheduled_message:{rule_id}"
    state_manager.set_state(user_id, chat_id, state, message, state_type="scheduled")
    asyncio.create_task(cancel_state_after_timeout(user_id, chat_id))

    prompt_text, prompt_buttons = format_scheduled_message_input_text(rule.id)
    await message.edit(prompt_text, buttons=prompt_buttons)


async def callback_edit_scheduled_config(event, config_id, session, message, data):
    config = session.query(ScheduledMessageConfig).get(int(config_id))
    if not config:
        await event.answer("配置不存在")
        return

    if isinstance(event.chat, types.Channel):
        if not await is_admin(event):
            await event.answer("只有管理员可以修改设置")
            return
        user_id = os.getenv('USER_ID')
    else:
        user_id = event.sender_id

    chat_id = abs(event.chat_id)
    state = f"edit_scheduled_message:{config.id}"
    state_manager.set_state(user_id, chat_id, state, message, state_type="scheduled")
    asyncio.create_task(cancel_state_after_timeout(user_id, chat_id))

    prompt_text, prompt_buttons = format_scheduled_message_input_text(config.rule_id, config=config)
    await message.edit(prompt_text, buttons=prompt_buttons)


async def callback_cancel_scheduled_message(event, rule_id, session, message, data):
    if isinstance(event.chat, types.Channel):
        user_id = os.getenv('USER_ID')
    else:
        user_id = event.sender_id

    chat_id = abs(event.chat_id)
    state_manager.clear_state(user_id, chat_id)
    await event.edit(
        SCHEDULED_MESSAGE_SETTINGS_TEXT,
        buttons=await create_scheduled_settings_buttons(int(rule_id)),
        link_preview=False
    )
    await event.answer("已取消定时发布配置")


async def cancel_state_after_timeout(user_id: int, chat_id: int, timeout_minutes: int = 5):
    await asyncio.sleep(timeout_minutes * 60)
    current_state, _, _ = state_manager.get_state(user_id, chat_id)
    if current_state and current_state.startswith(("add_scheduled_message:", "edit_scheduled_message:")):
        state_manager.clear_state(user_id, chat_id)


async def callback_toggle_scheduled_config(event, config_id, session, message, data):
    config = session.query(ScheduledMessageConfig).get(int(config_id))
    if not config:
        await event.answer("配置不存在")
        return

    await event.edit(
        scheduled_config_detail_text(config),
        buttons=await create_scheduled_config_details_buttons(config.id)
    )


async def callback_toggle_scheduled_config_status(event, config_id, session, message, data):
    try:
        config = session.query(ScheduledMessageConfig).get(int(config_id))
        if not config:
            await event.answer("配置不存在")
            return

        config.enabled = not config.enabled
        config.next_run_at = None
        rule = session.query(ForwardRule).get(config.rule_id)
        affected_rule_ids = {config.rule_id}

        if rule and rule.enable_sync:
            sync_rules = session.query(RuleSync).filter(RuleSync.rule_id == rule.id).all()
            for sync_rule in sync_rules:
                target_config = session.query(ScheduledMessageConfig).filter_by(
                    rule_id=sync_rule.sync_rule_id,
                    schedule_type=config.schedule_type,
                    interval_value=config.interval_value,
                    daily_time=config.daily_time,
                    message_text=config.message_text,
                ).first()
                if target_config:
                    target_config.enabled = config.enabled
                    target_config.next_run_at = None
                    affected_rule_ids.add(sync_rule.sync_rule_id)

        session.commit()
        await refresh_scheduled_rules(list(affected_rule_ids))

        await event.edit(
            scheduled_config_detail_text(config),
            buttons=await create_scheduled_config_details_buttons(config.id)
        )
        await event.answer(f"已{'启用' if config.enabled else '停用'}定时发布")
    except Exception as exc:
        session.rollback()
        logger.error(f"切换定时发布状态失败: {exc}")
        logger.error(traceback.format_exc())
        await event.answer("处理请求时出错，请检查日志")


async def callback_delete_scheduled_config(event, config_id, session, message, data):
    try:
        config = session.query(ScheduledMessageConfig).get(int(config_id))
        if not config:
            await event.answer("配置不存在")
            return

        rule_id = config.rule_id
        rule = session.query(ForwardRule).get(rule_id)
        affected_rule_ids = {rule_id}
        signature = (
            config.schedule_type,
            config.interval_value,
            config.daily_time,
            config.message_text,
        )

        if rule and rule.enable_sync:
            sync_rules = session.query(RuleSync).filter(RuleSync.rule_id == rule.id).all()
            for sync_rule in sync_rules:
                target_config = session.query(ScheduledMessageConfig).filter_by(
                    rule_id=sync_rule.sync_rule_id,
                    schedule_type=signature[0],
                    interval_value=signature[1],
                    daily_time=signature[2],
                    message_text=signature[3],
                ).first()
                if target_config:
                    session.delete(target_config)
                    affected_rule_ids.add(sync_rule.sync_rule_id)

        session.delete(config)
        session.commit()

        scheduler = await get_scheduled_message_scheduler()
        if scheduler:
            await scheduler.delete_config(int(config_id))
            for sync_rule_id in affected_rule_ids:
                await scheduler.refresh_rule(sync_rule_id)

        await event.edit(
            SCHEDULED_MESSAGE_SETTINGS_TEXT,
            buttons=await create_scheduled_settings_buttons(rule_id),
            link_preview=False
        )
        await event.answer("已删除定时发布配置")
    except Exception as exc:
        session.rollback()
        logger.error(f"删除定时发布配置失败: {exc}")
        logger.error(traceback.format_exc())
        await event.answer("处理请求时出错，请检查日志")


async def callback_scheduled_page(event, rule_id_data, session, message, data):
    try:
        rule_id, page = map(int, rule_id_data.split(":"))
        await event.edit(
            SCHEDULED_MESSAGE_SETTINGS_TEXT,
            buttons=await create_scheduled_settings_buttons(rule_id, page),
            link_preview=False
        )
        await event.answer(f"第 {page + 1} 页")
    except Exception as exc:
        logger.error(f"切换定时发布分页失败: {exc}")
        logger.error(traceback.format_exc())
        await event.answer("处理请求时出错，请检查日志")
