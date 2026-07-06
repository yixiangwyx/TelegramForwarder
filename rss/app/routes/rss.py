from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from models.models import (
    get_session,
    User,
    RSSConfig,
    ForwardRule,
    RSSPattern,
    Chat,
    Keyword,
    ReplaceRule,
    RuleSync,
    ScheduledMessageConfig,
)
from models.db_operations import DBOperations
from typing import Optional, List
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError
from .auth import get_current_user
from feedgen.feed import FeedGenerator
from datetime import datetime
import logging
import base64
import re
import json
from utils.common import get_db_ops
import os
import aiohttp
from utils.constants import RSS_HOST, RSS_PORT, RSS_BASE_URL
from utils.settings import load_ai_models
from enums.enums import ForwardMode, PreviewMode, MessageMode, AddMode, HandleMode

# 配置日志
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rss")
templates = Jinja2Templates(directory="rss/app/templates")
db_ops = None
AI_MODELS = load_ai_models()

FORWARD_MODE_LABELS = {
    ForwardMode.BLACKLIST: "仅黑名单",
    ForwardMode.WHITELIST: "仅白名单",
    ForwardMode.BLACKLIST_THEN_WHITELIST: "先黑名单后白名单",
    ForwardMode.WHITELIST_THEN_BLACKLIST: "先白名单后黑名单",
}

PREVIEW_MODE_LABELS = {
    PreviewMode.ON: "开启",
    PreviewMode.OFF: "关闭",
    PreviewMode.FOLLOW: "跟随原消息",
}

MESSAGE_MODE_LABELS = {
    MessageMode.MARKDOWN: "Markdown",
    MessageMode.HTML: "HTML",
}

ADD_MODE_LABELS = {
    AddMode.BLACKLIST: "黑名单",
    AddMode.WHITELIST: "白名单",
}

HANDLE_MODE_LABELS = {
    HandleMode.FORWARD: "转发模式",
    HandleMode.EDIT: "编辑模式",
}

async def init_db_ops():
    global db_ops
    if db_ops is None:
        db_ops = await get_db_ops()
    return db_ops


def _serialize_chat(chat: Chat):
    return {
        "id": chat.id,
        "telegram_chat_id": chat.telegram_chat_id,
        "name": chat.name or "未命名聊天",
    }


def _enum_name(value):
    return value.name if value is not None else None


def _serialize_rule_summary(rule: ForwardRule):
    return {
        "id": rule.id,
        "source_chat": _serialize_chat(rule.source_chat) if rule.source_chat else None,
        "target_chat": _serialize_chat(rule.target_chat) if rule.target_chat else None,
        "enable_rule": rule.enable_rule,
        "forward_mode": _enum_name(rule.forward_mode),
        "forward_mode_label": FORWARD_MODE_LABELS.get(rule.forward_mode, str(rule.forward_mode)),
        "add_mode": _enum_name(rule.add_mode),
        "add_mode_label": ADD_MODE_LABELS.get(rule.add_mode, str(rule.add_mode)),
        "message_mode": _enum_name(rule.message_mode),
        "message_mode_label": MESSAGE_MODE_LABELS.get(rule.message_mode, str(rule.message_mode)),
        "handle_mode": _enum_name(rule.handle_mode),
        "handle_mode_label": HANDLE_MODE_LABELS.get(rule.handle_mode, str(rule.handle_mode)),
        "keyword_count": len(rule.keywords or []),
        "replace_rule_count": len(rule.replace_rules or []),
        "rss_config_id": rule.rss_config.id if rule.rss_config else None,
        "rss_enabled": rule.rss_config.enable_rss if rule.rss_config else False,
        "enable_reply_forward": rule.enable_reply_forward,
        "is_ai": rule.is_ai,
        "enable_delay": rule.enable_delay,
        "enable_sync": rule.enable_sync,
        "scheduled_message_count": len(rule.scheduled_message_configs or []),
    }


def _serialize_rule_detail(rule: ForwardRule):
    return {
        "id": rule.id,
        "source_chat_id": rule.source_chat_id,
        "target_chat_id": rule.target_chat_id,
        "source_chat": _serialize_chat(rule.source_chat) if rule.source_chat else None,
        "target_chat": _serialize_chat(rule.target_chat) if rule.target_chat else None,
        "enable_rule": rule.enable_rule,
        "forward_mode": _enum_name(rule.forward_mode),
        "add_mode": _enum_name(rule.add_mode),
        "use_bot": rule.use_bot,
        "message_mode": _enum_name(rule.message_mode),
        "is_preview": _enum_name(rule.is_preview),
        "handle_mode": _enum_name(rule.handle_mode),
        "is_replace": rule.is_replace,
        "is_original_link": rule.is_original_link,
        "is_original_sender": rule.is_original_sender,
        "is_original_time": rule.is_original_time,
        "is_delete_original": rule.is_delete_original,
        "is_filter_user_info": rule.is_filter_user_info,
        "enable_comment_button": rule.enable_comment_button,
        "enable_delay": rule.enable_delay,
        "delay_seconds": rule.delay_seconds,
        "only_rss": rule.only_rss,
        "enable_sync": rule.enable_sync,
        "is_ai": rule.is_ai,
        "ai_model": rule.ai_model or "",
        "ai_prompt": rule.ai_prompt or "",
        "enable_ai_upload_image": rule.enable_ai_upload_image,
        "is_keyword_after_ai": rule.is_keyword_after_ai,
        "is_summary": rule.is_summary,
        "summary_time": rule.summary_time or "",
        "summary_prompt": rule.summary_prompt or "",
        "enable_reply_forward": rule.enable_reply_forward,
        "reply_forward_ai_check": rule.reply_forward_ai_check,
        "scheduled_message_configs": [
            {
                "id": config.id,
                "enabled": config.enabled,
                "schedule_type": config.schedule_type,
                "interval_value": config.interval_value,
                "daily_time": config.daily_time or "",
                "message_text": config.message_text or "",
                "next_run_at": config.next_run_at or "",
                "last_sent_at": config.last_sent_at or "",
            }
            for config in rule.scheduled_message_configs
        ],
        "keywords": [
            {
                "id": keyword.id,
                "keyword": keyword.keyword or "",
                "is_regex": keyword.is_regex,
                "is_blacklist": keyword.is_blacklist,
            }
            for keyword in rule.keywords
        ],
        "replace_rules": [
            {
                "id": replace_rule.id,
                "pattern": replace_rule.pattern,
                "content": replace_rule.content or "",
            }
            for replace_rule in rule.replace_rules
        ],
        "sync_rule_ids": [sync.sync_rule_id for sync in rule.rule_syncs],
    }


def _rule_form_options():
    return {
        "forward_modes": [
            {"value": enum_value.name, "label": label}
            for enum_value, label in FORWARD_MODE_LABELS.items()
        ],
        "add_modes": [
            {"value": enum_value.name, "label": label}
            for enum_value, label in ADD_MODE_LABELS.items()
        ],
        "message_modes": [
            {"value": enum_value.name, "label": label}
            for enum_value, label in MESSAGE_MODE_LABELS.items()
        ],
        "preview_modes": [
            {"value": enum_value.name, "label": label}
            for enum_value, label in PREVIEW_MODE_LABELS.items()
        ],
        "handle_modes": [
            {"value": enum_value.name, "label": label}
            for enum_value, label in HANDLE_MODE_LABELS.items()
        ],
    }


def _parse_enum(enum_cls, raw_value, default):
    if raw_value is None or raw_value == "":
        return default
    try:
        return enum_cls[raw_value]
    except KeyError:
        for item in enum_cls:
            if item.value == raw_value:
                return item
    return default


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_json_list(value):
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _get_or_create_chat(session, manual_chat_id, manual_name, selected_chat_id):
    chat = None

    if manual_chat_id:
        telegram_chat_id = str(manual_chat_id).strip()
        chat = session.query(Chat).filter(Chat.telegram_chat_id == telegram_chat_id).first()
        if chat is None:
            chat = Chat(
                telegram_chat_id=telegram_chat_id,
                name=(manual_name or telegram_chat_id).strip(),
            )
            session.add(chat)
            session.flush()
        elif manual_name and manual_name.strip() and chat.name != manual_name.strip():
            chat.name = manual_name.strip()
    elif selected_chat_id:
        chat = session.query(Chat).filter(Chat.id == int(selected_chat_id)).first()

    return chat

@router.get("/dashboard", response_class=HTMLResponse)
async def rss_dashboard(request: Request, user = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        await init_db_ops()
        
        success_message = request.query_params.get("success")
        error_message = request.query_params.get("error")

        # 获取所有RSS配置
        rss_configs = db_session.query(RSSConfig).options(
            joinedload(RSSConfig.rule)
        ).all()
        
        # 将 RSSConfig 对象转换为字典列表
        configs_list = []
        for config in rss_configs:
            # 处理AI提取提示词，使用Base64编码避免JSON解析问题
            ai_prompt = config.ai_extract_prompt
            ai_prompt_encoded = None
            if ai_prompt:
                # 使用Base64编码处理提示词
                ai_prompt_encoded = base64.b64encode(ai_prompt.encode('utf-8')).decode('utf-8')
                # 添加标记，表示这是Base64编码的内容
                ai_prompt_encoded = "BASE64:" + ai_prompt_encoded
            
            configs_list.append({
                "id": config.id,
                "rule_id": config.rule_id,
                "enable_rss": config.enable_rss,
                "rule_title": config.rule_title,
                "rule_description": config.rule_description,
                "language": config.language,
                "max_items": config.max_items,
                "is_auto_title": config.is_auto_title,
                "is_auto_content": config.is_auto_content,
                "is_ai_extract": config.is_ai_extract,
                "ai_extract_prompt": ai_prompt_encoded,
                "is_auto_markdown_to_html": config.is_auto_markdown_to_html,
                "enable_custom_title_pattern": config.enable_custom_title_pattern,
                "enable_custom_content_pattern": config.enable_custom_content_pattern
            })
        
        # 获取所有转发规则（用于创建新的RSS配置）
        rules = db_session.query(ForwardRule).options(
            joinedload(ForwardRule.source_chat),
            joinedload(ForwardRule.target_chat),
            joinedload(ForwardRule.rss_config),
            joinedload(ForwardRule.keywords),
            joinedload(ForwardRule.replace_rules),
            joinedload(ForwardRule.scheduled_message_configs),
        ).all()
        
        # 将 ForwardRule 对象转换为字典列表
        rules_list = []
        for rule in rules:
            rules_list.append({
                "id": rule.id,
                "source_chat": {
                    "id": rule.source_chat.id,
                    "name": rule.source_chat.name
                } if rule.source_chat else None,
                "target_chat": {
                    "id": rule.target_chat.id,
                    "name": rule.target_chat.name
                } if rule.target_chat else None
            })

        forward_rules = [_serialize_rule_summary(rule) for rule in rules]
        known_chats = [
            _serialize_chat(chat)
            for chat in db_session.query(Chat).order_by(Chat.name.asc(), Chat.id.asc()).all()
        ]
        
        return templates.TemplateResponse(
            "rss_dashboard.html", 
            {
                "request": request,
                "user": user,
                "error": error_message,
                "success": success_message,
                "rss_configs": configs_list,
                "rules": rules_list,
                "forward_rules": forward_rules,
                "known_chats": known_chats,
                "rule_form_options": _rule_form_options(),
                "ai_models": AI_MODELS,
                "rss_base_url": RSS_BASE_URL or ""
            }
        )
    finally:
        db_session.close()


@router.get("/rule/{rule_id}")
async def get_rule(rule_id: int, user = Depends(get_current_user)):
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)

    db_session = get_session()
    try:
        rule = db_session.query(ForwardRule).options(
            joinedload(ForwardRule.source_chat),
            joinedload(ForwardRule.target_chat),
            joinedload(ForwardRule.keywords),
            joinedload(ForwardRule.replace_rules),
            joinedload(ForwardRule.rule_syncs),
            joinedload(ForwardRule.scheduled_message_configs),
        ).filter(ForwardRule.id == rule_id).first()

        if not rule:
            return JSONResponse({"success": False, "message": "规则不存在"}, status_code=status.HTTP_404_NOT_FOUND)

        return JSONResponse({"success": True, "rule": _serialize_rule_detail(rule)})
    finally:
        db_session.close()


@router.post("/rule")
async def save_rule(request: Request, user = Depends(get_current_user)):
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)

    payload = await request.json()
    rule_id = _parse_int(payload.get("rule_id"))
    keywords_data = _parse_json_list(payload.get("keywords"))
    replace_rules_data = _parse_json_list(payload.get("replace_rules"))
    scheduled_message_configs_data = _parse_json_list(payload.get("scheduled_message_configs"))
    sync_rule_ids = [rule_id_value for rule_id_value in (_parse_int(item) for item in _parse_json_list(payload.get("sync_rule_ids"))) if rule_id_value]

    db_session = get_session()
    try:
        await init_db_ops()

        rule = None
        if rule_id:
            rule = db_session.query(ForwardRule).filter(ForwardRule.id == rule_id).first()
            if not rule:
                return JSONResponse({"success": False, "message": "规则不存在"}, status_code=status.HTTP_404_NOT_FOUND)

        source_chat = _get_or_create_chat(
            db_session,
            payload.get("source_manual_chat_id"),
            payload.get("source_manual_chat_name"),
            payload.get("source_chat_id"),
        )
        target_chat = _get_or_create_chat(
            db_session,
            payload.get("target_manual_chat_id"),
            payload.get("target_manual_chat_name"),
            payload.get("target_chat_id"),
        )

        if rule is None and (source_chat is None or target_chat is None):
            return JSONResponse({"success": False, "message": "请先选择或填写源聊天与目标聊天"})

        if rule is None:
            rule = ForwardRule(
                source_chat_id=source_chat.id,
                target_chat_id=target_chat.id,
            )
            db_session.add(rule)
            db_session.flush()
        else:
            if source_chat is not None:
                rule.source_chat_id = source_chat.id
            if target_chat is not None:
                rule.target_chat_id = target_chat.id

        if target_chat and source_chat and not target_chat.current_add_id:
            target_chat.current_add_id = source_chat.telegram_chat_id

        rule.enable_rule = _parse_bool(payload.get("enable_rule"), rule.enable_rule if rule_id else True)
        rule.forward_mode = _parse_enum(ForwardMode, payload.get("forward_mode"), rule.forward_mode or ForwardMode.BLACKLIST)
        rule.add_mode = _parse_enum(AddMode, payload.get("add_mode"), rule.add_mode or AddMode.BLACKLIST)
        rule.use_bot = _parse_bool(payload.get("use_bot"), rule.use_bot if rule_id else True)
        rule.message_mode = _parse_enum(MessageMode, payload.get("message_mode"), rule.message_mode or MessageMode.MARKDOWN)
        rule.is_preview = _parse_enum(PreviewMode, payload.get("is_preview"), rule.is_preview or PreviewMode.FOLLOW)
        rule.handle_mode = _parse_enum(HandleMode, payload.get("handle_mode"), rule.handle_mode or HandleMode.FORWARD)
        rule.is_replace = _parse_bool(payload.get("is_replace"), rule.is_replace)
        rule.is_original_link = _parse_bool(payload.get("is_original_link"), rule.is_original_link)
        rule.is_original_sender = _parse_bool(payload.get("is_original_sender"), rule.is_original_sender)
        rule.is_original_time = _parse_bool(payload.get("is_original_time"), rule.is_original_time)
        rule.is_delete_original = _parse_bool(payload.get("is_delete_original"), rule.is_delete_original)
        rule.is_filter_user_info = _parse_bool(payload.get("is_filter_user_info"), rule.is_filter_user_info)
        rule.enable_comment_button = _parse_bool(payload.get("enable_comment_button"), rule.enable_comment_button)
        rule.enable_delay = _parse_bool(payload.get("enable_delay"), rule.enable_delay)
        rule.delay_seconds = _parse_int(payload.get("delay_seconds"), rule.delay_seconds or 5) or 5
        rule.only_rss = _parse_bool(payload.get("only_rss"), rule.only_rss)
        rule.is_ai = _parse_bool(payload.get("is_ai"), rule.is_ai)
        rule.ai_model = (payload.get("ai_model") or "").strip() or None
        rule.ai_prompt = payload.get("ai_prompt") or None
        rule.enable_ai_upload_image = _parse_bool(payload.get("enable_ai_upload_image"), rule.enable_ai_upload_image)
        rule.is_keyword_after_ai = _parse_bool(payload.get("is_keyword_after_ai"), rule.is_keyword_after_ai)
        rule.is_summary = _parse_bool(payload.get("is_summary"), rule.is_summary)
        rule.summary_time = (payload.get("summary_time") or rule.summary_time or "07:00").strip()
        rule.summary_prompt = payload.get("summary_prompt") or None
        rule.enable_reply_forward = _parse_bool(payload.get("enable_reply_forward"), rule.enable_reply_forward)
        rule.reply_forward_ai_check = _parse_bool(payload.get("reply_forward_ai_check"), rule.reply_forward_ai_check if rule_id else True)
        rule.enable_sync = _parse_bool(payload.get("enable_sync"), rule.enable_sync)

        db_session.flush()

        db_session.query(Keyword).filter(Keyword.rule_id == rule.id).delete(synchronize_session=False)
        seen_keywords = set()
        for item in keywords_data:
            keyword_text = (item.get("keyword") or "").strip()
            if not keyword_text:
                continue
            key = (
                keyword_text,
                _parse_bool(item.get("is_regex")),
                _parse_bool(item.get("is_blacklist")),
            )
            if key in seen_keywords:
                continue
            seen_keywords.add(key)
            db_session.add(
                Keyword(
                    rule_id=rule.id,
                    keyword=keyword_text,
                    is_regex=key[1],
                    is_blacklist=key[2],
                )
            )

        db_session.query(ReplaceRule).filter(ReplaceRule.rule_id == rule.id).delete(synchronize_session=False)
        seen_replace_rules = set()
        for item in replace_rules_data:
            pattern = (item.get("pattern") or "").strip()
            content = item.get("content") or ""
            if not pattern:
                continue
            key = (pattern, content)
            if key in seen_replace_rules:
                continue
            seen_replace_rules.add(key)
            db_session.add(
                ReplaceRule(
                    rule_id=rule.id,
                    pattern=pattern,
                    content=content,
                )
            )

        db_session.query(RuleSync).filter(RuleSync.rule_id == rule.id).delete(synchronize_session=False)
        if rule.enable_sync:
            for sync_rule_id in sorted(set(sync_rule_ids)):
                if sync_rule_id == rule.id:
                    continue
                if db_session.query(ForwardRule).filter(ForwardRule.id == sync_rule_id).first():
                    db_session.add(RuleSync(rule_id=rule.id, sync_rule_id=sync_rule_id))

        db_session.query(ScheduledMessageConfig).filter(
            ScheduledMessageConfig.rule_id == rule.id
        ).delete(synchronize_session=False)
        for item in scheduled_message_configs_data:
            schedule_type = (item.get("schedule_type") or "").strip()
            message_text = (item.get("message_text") or "").strip()
            if schedule_type not in {"daily", "interval_hours", "interval_minutes"} or not message_text:
                continue

            interval_value = _parse_int(item.get("interval_value"))
            daily_time = (item.get("daily_time") or "").strip() or None
            if schedule_type == "daily":
                if not daily_time:
                    continue
                interval_value = None
            else:
                if interval_value is None or interval_value <= 0:
                    continue
                daily_time = None

            db_session.add(
                ScheduledMessageConfig(
                    rule_id=rule.id,
                    enabled=_parse_bool(item.get("enabled"), True),
                    schedule_type=schedule_type,
                    interval_value=interval_value,
                    daily_time=daily_time,
                    message_text=message_text,
                    next_run_at=None,
                )
            )

        db_session.commit()

        try:
            await db_ops.sync_to_server(db_session, rule.id)
        except Exception as exc:
            logger.warning(f"规则保存后同步UFB配置失败: {exc}")

        return JSONResponse({"success": True, "message": "规则保存成功", "rule_id": rule.id})
    except IntegrityError:
        db_session.rollback()
        return JSONResponse({"success": False, "message": "已存在相同的源聊天和目标聊天规则"})
    except Exception as exc:
        db_session.rollback()
        logger.error(f"保存规则失败: {exc}", exc_info=True)
        return JSONResponse({"success": False, "message": f"保存规则失败: {exc}"})
    finally:
        db_session.close()


@router.delete("/rule/{rule_id}")
async def delete_rule(rule_id: int, user = Depends(get_current_user)):
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)

    db_session = get_session()
    try:
        rule = db_session.query(ForwardRule).options(joinedload(ForwardRule.rss_config)).filter(ForwardRule.id == rule_id).first()
        if not rule:
            return JSONResponse({"success": False, "message": "规则不存在"}, status_code=status.HTTP_404_NOT_FOUND)

        if rule.rss_config:
            try:
                rss_url = f"http://{RSS_HOST}:{RSS_PORT}/api/rule/{rule.id}"
                async with aiohttp.ClientSession() as client_session:
                    await client_session.delete(rss_url)
            except Exception as exc:
                logger.warning(f"删除规则 {rule.id} 的RSS缓存时出错: {exc}")

        db_session.delete(rule)
        db_session.commit()
        return JSONResponse({"success": True, "message": "规则删除成功"})
    except Exception as exc:
        db_session.rollback()
        logger.error(f"删除规则失败: {exc}", exc_info=True)
        return JSONResponse({"success": False, "message": f"删除规则失败: {exc}"})
    finally:
        db_session.close()

@router.post("/config", response_class=JSONResponse)
async def rss_config_save(
    request: Request,
    user = Depends(get_current_user),
    config_id: Optional[str] = Form(None),
    rule_id: int = Form(...),
    enable_rss: bool = Form(True),
    rule_title: str = Form(""),
    rule_description: str = Form(""),
    language: str = Form("zh-CN"),
    max_items: int = Form(50),
    is_auto_title: bool = Form(False),
    is_auto_content: bool = Form(False),
    is_ai_extract: bool = Form(False),
    ai_extract_prompt: str = Form(""),
    is_auto_markdown_to_html: bool = Form(False),
    enable_custom_title_pattern: bool = Form(False),
    enable_custom_content_pattern: bool = Form(False)
):
    if not user:
        return JSONResponse(content={"success": False, "message": "未登录"})
    
    # 记录接收到的AI提取提示词内容，帮助调试
    logger.info(f"接收到的AI提取提示词字符数: {len(ai_extract_prompt)}")
    
    # 初始化数据库操作
    await init_db_ops()
    
    db_session = get_session()
    try:
        # 创建或更新RSS配置
        # 如果有config_id，表示更新
        if config_id and config_id.strip():
            config_id = int(config_id)
            # 检查配置是否存在
            rss_config = db_session.query(RSSConfig).filter(RSSConfig.id == config_id).first()
            if not rss_config:
                return JSONResponse(content={"success": False, "message": "配置不存在"})
            
            # 更新配置
            rss_config.rule_id = rule_id
            rss_config.enable_rss = enable_rss
            rss_config.rule_title = rule_title
            rss_config.rule_description = rule_description
            rss_config.language = language
            rss_config.max_items = max_items
            rss_config.is_auto_title = is_auto_title
            rss_config.is_auto_content = is_auto_content
            rss_config.is_ai_extract = is_ai_extract
            rss_config.ai_extract_prompt = ai_extract_prompt
            rss_config.is_auto_markdown_to_html = is_auto_markdown_to_html
            rss_config.enable_custom_title_pattern = enable_custom_title_pattern
            rss_config.enable_custom_content_pattern = enable_custom_content_pattern
        else:
            # 检查是否已经存在该规则的配置
            existing_config = db_session.query(RSSConfig).filter(RSSConfig.rule_id == rule_id).first()
            if existing_config:
                return JSONResponse(content={"success": False, "message": "该规则已经存在RSS配置"})
            
            # 创建新配置
            rss_config = RSSConfig(
                rule_id=rule_id,
                enable_rss=enable_rss,
                rule_title=rule_title,
                rule_description=rule_description,
                language=language,
                max_items=max_items,
                is_auto_title=is_auto_title,
                is_auto_content=is_auto_content,
                is_ai_extract=is_ai_extract,
                ai_extract_prompt=ai_extract_prompt,
                is_auto_markdown_to_html=is_auto_markdown_to_html,
                enable_custom_title_pattern=enable_custom_title_pattern,
                enable_custom_content_pattern=enable_custom_content_pattern
            )
        
        # 保存配置
        db_session.add(rss_config)
        db_session.commit()
        
        return JSONResponse({
            "success": True, 
            "message": "RSS 配置已保存",
            "config_id": rss_config.id,
            "rule_id": rss_config.rule_id
        })
    except Exception as e:
        return JSONResponse({"success": False, "message": f"保存配置失败: {str(e)}"})
    finally:
        db_session.close()

@router.get("/toggle/{rule_id}")
async def toggle_rss(rule_id: int, user = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        db_ops_instance = await init_db_ops()
        
        # 获取配置
        config = await db_ops_instance.get_rss_config(db_session, rule_id)
        if not config:
            return RedirectResponse(
                url="/rss/dashboard?error=配置不存在", 
                status_code=status.HTTP_302_FOUND
            )
        
        # 切换启用/禁用状态
        await db_ops_instance.update_rss_config(
            db_session,
            rule_id,
            enable_rss=not config.enable_rss
        )
        
        return RedirectResponse(
            url="/rss/dashboard?success=RSS状态已切换", 
            status_code=status.HTTP_302_FOUND
        )
    finally:
        db_session.close()

@router.get("/delete/{rule_id}")
async def delete_rss(rule_id: int, user = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        db_ops_instance = await init_db_ops()
        
        # 删除配置
        config_deleted = await db_ops_instance.delete_rss_config(db_session, rule_id)
        
        if config_deleted:
            # 删除关联的媒体和数据文件
            try:
                logger.info(f"开始删除规则 {rule_id} 的媒体和数据文件")
                # 构建删除API的URL
                rss_url = f"http://{RSS_HOST}:{RSS_PORT}/api/rule/{rule_id}"
                
                # 调用删除API
                async with aiohttp.ClientSession() as client_session:
                    async with client_session.delete(rss_url) as response:
                        if response.status == 200:
                            logger.info(f"成功删除规则 {rule_id} 的媒体和数据文件")
                        else:
                            response_text = await response.text()
                            logger.warning(f"删除规则 {rule_id} 的媒体和数据文件失败, 状态码: {response.status}, 响应: {response_text}")
            except Exception as e:
                logger.error(f"调用删除媒体文件API时出错: {str(e)}")
                # 不影响主流程，继续执行
        
        return RedirectResponse(
            url="/rss/dashboard?success=RSS配置已删除", 
            status_code=status.HTTP_302_FOUND
        )
    finally:
        db_session.close()

@router.get("/patterns/{config_id}")
async def get_patterns(config_id: int, user = Depends(get_current_user)):
    """获取指定RSS配置的所有模式"""
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        db_ops_instance = await init_db_ops()
        
        # 获取所有正则表达式数据
        config = await db_ops_instance.get_rss_config_with_patterns(db_session, config_id)
        if not config:
            return JSONResponse({"success": False, "message": "配置不存在"}, status_code=status.HTTP_404_NOT_FOUND)
        
        # 将模式转换为JSON格式
        patterns = []
        for pattern in config.patterns:
            patterns.append({
                "id": pattern.id,
                "pattern": pattern.pattern,
                "pattern_type": pattern.pattern_type,
                "priority": pattern.priority
            })
        
        return JSONResponse({"success": True, "patterns": patterns})
    finally:
        db_session.close()

@router.post("/pattern")
async def save_pattern(
    request: Request,
    user = Depends(get_current_user),
    pattern_id: Optional[str] = Form(None),
    rss_config_id: int = Form(...),
    pattern: str = Form(...),
    pattern_type: str = Form(...),
    priority: int = Form(0)
):
    """保存模式"""
    logger.info(f"开始保存模式，参数：config_id={rss_config_id}, pattern={pattern}, type={pattern_type}, priority={priority}")
    
    if not user:
        logger.warning("未登录的访问尝试")
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        db_ops_instance = await init_db_ops()
        
        # 检查RSS配置是否存在
        config = await db_ops_instance.get_rss_config(db_session, rss_config_id)
        if not config:
            logger.error(f"RSS配置不存在：config_id={rss_config_id}")
            return JSONResponse({"success": False, "message": "RSS配置不存在"})
        
        logger.debug(f"找到RSS配置：{config}")
    
      
        logger.info("创建新模式")
        # 创建新模式
        try:
            pattern_obj = await db_ops_instance.create_rss_pattern(
                db_session,
                config.id,
                pattern=pattern,
                pattern_type=pattern_type,
                priority=priority
            )
            logger.info(f"新模式创建成功：{pattern_obj}")
            return JSONResponse({"success": True, "message": "模式已创建", "pattern_id": pattern_obj.id})
        except Exception as e:
            logger.error(f"创建模式失败：{str(e)}")
            raise
    except Exception as e:
        logger.error(f"保存模式时发生错误：{str(e)}", exc_info=True)
        return JSONResponse({"success": False, "message": f"保存模式失败: {str(e)}"})
    finally:
        db_session.close()

@router.delete("/pattern/{pattern_id}")
async def delete_pattern(pattern_id: int, user = Depends(get_current_user)):
    """删除模式"""
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        await init_db_ops()
        
        # 查询模式
        pattern = db_session.query(RSSPattern).filter(RSSPattern.id == pattern_id).first()
        if not pattern:
            return JSONResponse({"success": False, "message": "找不到该模式"})
        
        # 删除模式
        db_session.delete(pattern)
        db_session.commit()
        
        return JSONResponse({"success": True, "message": "模式删除成功"})
    except Exception as e:
        db_session.rollback()
        logger.error(f"删除模式时出错: {str(e)}")
        return JSONResponse({"success": False, "message": f"删除模式失败: {str(e)}"})
    finally:
        db_session.close()

@router.delete("/patterns/{config_id}")
async def delete_all_patterns(config_id: int, user = Depends(get_current_user)):
    """删除配置的所有模式，通常在更新前调用以便重建模式列表"""
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)
    
    db_session = get_session()
    try:
        # 初始化数据库操作对象
        await init_db_ops()
        
        # 查询并删除指定配置的所有模式
        patterns = db_session.query(RSSPattern).filter(RSSPattern.rss_config_id == config_id).all()
        count = len(patterns)
        for pattern in patterns:
            db_session.delete(pattern)
        
        db_session.commit()
        logger.info(f"已删除配置 {config_id} 的所有模式，共 {count} 个")
        
        return JSONResponse({"success": True, "message": f"已删除 {count} 个模式"})
    except Exception as e:
        db_session.rollback()
        logger.error(f"删除配置 {config_id} 的所有模式时出错: {str(e)}")
        return JSONResponse({"success": False, "message": f"删除所有模式失败: {str(e)}"})
    finally:
        db_session.close()

@router.post("/test-regex")
async def test_regex(user = Depends(get_current_user), 
                    pattern: str = Form(...), 
                    test_text: str = Form(...), 
                    pattern_type: str = Form(...)):
    """测试正则表达式匹配结果"""
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=status.HTTP_401_UNAUTHORIZED)
    
    try:
        
        
        # 记录测试信息
        logger.info(f"测试正则表达式: {pattern}")
        logger.info(f"测试类型: {pattern_type}")
        logger.info(f"测试文本长度: {len(test_text)} 字符")
        
        # 执行正则匹配
        match = re.search(pattern, test_text)
        
        # 检查是否有匹配
        if not match:
            return JSONResponse({
                "success": True,
                "matched": False,
                "message": "未找到匹配"
            })
            
        # 检查捕获组
        if not match.groups():
            return JSONResponse({
                "success": True,
                "matched": True,
                "has_groups": False,
                "message": "匹配成功，但没有捕获组。请使用括号 () 来创建捕获组。"
            })
            
        # 成功匹配且有捕获组
        extracted_content = match.group(1)
        
        # 返回匹配结果
        return JSONResponse({
            "success": True,
            "matched": True,
            "has_groups": True,
            "extracted": extracted_content,
            "message": "匹配成功！"
        })
        
    except Exception as e:
        logger.error(f"测试正则表达式时出错: {str(e)}")
        return JSONResponse({
            "success": False,
            "message": f"测试失败: {str(e)}"
        }) 
