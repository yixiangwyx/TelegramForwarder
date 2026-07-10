import logging
import os
import pytz
import asyncio
import apprise
import aiohttp
import json
from datetime import datetime
import traceback

from filters.base_filter import BaseFilter
from models.models import get_session, PushConfig
from enums.enums import PreviewMode

logger = logging.getLogger(__name__)

API_PUSH_SCHEMES = ("api://", "apis://")


def is_api_push_channel(value):
    return isinstance(value, str) and value.startswith(API_PUSH_SCHEMES)


def normalize_api_push_url(value):
    if not isinstance(value, str):
        return ""
    if value.startswith("api://"):
        return f"http://{value[len('api://'):]}"
    if value.startswith("apis://"):
        return f"https://{value[len('apis://'):]}"
    return value


def build_original_link(chat_id, message_id):
    chat_id_text = str(chat_id or "").strip()
    if chat_id_text.startswith("-100") and message_id:
        return f"https://t.me/c/{chat_id_text[4:]}/{message_id}"
    return ""


def safe_display_name(value, fallback=""):
    return str(value or "").strip() or fallback


def resolve_chat_name(context):
    rule = getattr(context, "rule", None)
    event = getattr(context, "event", None)
    source_chat = getattr(rule, "source_chat", None)
    if source_chat and getattr(source_chat, "name", None):
        return safe_display_name(source_chat.name)
    chat = getattr(event, "chat", None)
    if chat is not None:
        if getattr(chat, "title", None):
            return safe_display_name(chat.title)
        first_name = safe_display_name(getattr(chat, "first_name", ""))
        last_name = safe_display_name(getattr(chat, "last_name", ""))
        full_name = " ".join(item for item in [first_name, last_name] if item).strip()
        if full_name:
            return full_name
    return ""


def resolve_sender_metadata(event):
    sender_name = ""
    sender_id = ""
    sender_type = ""

    if hasattr(event.message, "sender_chat") and event.message.sender_chat:
        sender = event.message.sender_chat
        sender_name = safe_display_name(getattr(sender, "title", ""))
        sender_id = safe_display_name(getattr(sender, "id", ""))
        sender_type = "chat"
    elif getattr(event, "sender", None):
        sender = event.sender
        if getattr(sender, "title", None):
            sender_name = safe_display_name(sender.title)
        else:
            first_name = safe_display_name(getattr(sender, "first_name", ""))
            last_name = safe_display_name(getattr(sender, "last_name", ""))
            sender_name = " ".join(item for item in [first_name, last_name] if item).strip()
        sender_id = safe_display_name(getattr(sender, "id", ""))
        sender_type = "user"

    return {
        "sender_name": sender_name,
        "sender_id": sender_id,
        "sender_type": sender_type
    }

class PushFilter(BaseFilter):
    """
    推送过滤器，利用apprise库推送消息
    """
    
    async def _process(self, context):
        """
        推送消息
        
        Args:
            context: 消息上下文
            
        Returns:
            bool: 若消息应继续处理则返回True，否则返回False
        """
        rule = context.rule
        client = context.client
        event = context.event
        
        # 如果规则没有启用推送，直接返回
        if not rule.enable_push:
            logger.info('推送未启用，跳过推送')
            return True
        
        # 获取规则ID和所有启用的推送配置
        rule_id = rule.id
        session = get_session()
        
 
        logger.info(f"推送过滤器开始处理 - 规则ID: {rule_id}")
        logger.info(f"是否是媒体组: {context.is_media_group}")
        logger.info(f"媒体组消息数量: {len(context.media_group_messages) if context.media_group_messages else 0}")
        logger.info(f"已有媒体文件数量: {len(context.media_files) if context.media_files else 0}")
        logger.info(f"是否只推送不转发: {rule.enable_only_push}")
        
        # 跟踪已处理的文件
        processed_files = []
        
        try:
            # 获取所有启用的推送配置
            push_configs = session.query(PushConfig).filter(
                PushConfig.rule_id == rule_id,
                PushConfig.enable_push_channel == True
            ).all()
            
            if not push_configs:
                logger.info(f'规则 {rule_id} 没有启用的推送配置，跳过推送')
                return True
            
            # 对媒体组消息进行推送
            if context.is_media_group or (context.media_group_messages and context.skipped_media):
                processed_files = await self._push_media_group(context, push_configs)
            # 对单条媒体消息进行推送
            elif context.media_files or context.skipped_media:
                processed_files = await self._push_single_media(context, push_configs)
            # 对纯文本消息进行推送
            else:
                processed_files = await self._push_text_message(context, push_configs)
            
            logger.info(f'推送已发送到 {len(push_configs)} 个配置')
            return True
            
        except Exception as e:
            logger.error(f'推送过滤器处理出错: {str(e)}')
            logger.error(traceback.format_exc())
            context.errors.append(f"推送错误: {str(e)}")
            return False
        finally:
            session.close()
            
            # 只清理已处理的媒体文件
            if processed_files:
                logger.info(f'清理已处理的媒体文件，共 {len(processed_files)} 个')
                for file_path in processed_files:
                    try:
                        if os.path.exists(str(file_path)):
                            os.remove(file_path)
                            logger.info(f'删除已处理的媒体文件: {file_path}')
                    except Exception as e:
                        logger.error(f'删除媒体文件失败: {str(e)}')
    
    async def _push_media_group(self, context, push_configs):
        """推送媒体组消息"""
        rule = context.rule
        client = context.client
        event = context.event
        
        # 初始化文件列表
        files = []
        need_cleanup = False
        
        try:
            # 如果没有媒体组消息（都超限了），发送文本和提示
            if not context.media_group_messages and context.skipped_media:
                logger.info(f'所有媒体都超限，发送文本和提示')
                # 构建提示信息
                text_to_send = context.message_text or ''
                
                # 设置原始消息链接
                if rule.is_original_link:
                    context.original_link = f"\n原始消息: https://t.me/c/{str(event.chat_id)[4:]}/{event.message.id}"
                
                # 添加每个超限文件的信息
                for message, size, name in context.skipped_media:
                    text_to_send += f"\n\n⚠️ 媒体文件 {name if name else '未命名文件'} ({size}MB) 超过大小限制"
                
                # 组合完整文本
                if rule.is_original_sender:
                    text_to_send = context.sender_info + text_to_send
                if rule.is_original_time:
                    text_to_send += context.time_info
                if rule.is_original_link:
                    text_to_send += context.original_link
                
                # 发送文本推送
                await self._send_push_notification(push_configs, text_to_send, context=context)
                return
            
            # 检查是否有媒体组消息但没有媒体文件（这是关键修复）
            if context.media_group_messages and not context.media_files:
                logger.info(f'检测到媒体组消息但没有媒体文件，开始下载...')
                need_cleanup = True
                for message in context.media_group_messages:
                    if message.media:
                        file_path = await message.download_media(os.path.join(os.getcwd(), 'temp'))
                        if file_path:
                            files.append(file_path)
                            logger.info(f'已下载媒体组文件: {file_path}')
            # 如果SenderFilter已经下载了文件，使用它们
            elif context.media_files:
                logger.info(f'使用SenderFilter已下载的文件: {len(context.media_files)}个')
                files = context.media_files
            # 否则，需要自己下载文件
            elif rule.enable_only_push:
                logger.info(f'需要自己下载文件，开始下载媒体组消息...')
                need_cleanup = True
                for message in context.media_group_messages:
                    if message.media:
                        file_path = await message.download_media(os.path.join(os.getcwd(), 'temp'))
                        if file_path:
                            files.append(file_path)
                            logger.info(f'已下载媒体文件: {file_path}')
            
            # 如果有可用的媒体文件，构建推送内容
            if files:
                # 添加发送者信息和消息文本
                caption_text = ""
                if rule.is_original_sender and context.sender_info:
                    caption_text += context.sender_info
                caption_text += context.message_text or ""
                
                # 如果有超限文件，添加提示信息
                for message, size, name in context.skipped_media:
                    caption_text += f"\n\n⚠️ 媒体文件 {name if name else '未命名文件'} ({size}MB) 超过大小限制"
                
                # 添加原始链接
                if rule.is_original_link and context.skipped_media:
                    original_link = f"\n原始消息: https://t.me/c/{str(event.chat_id)[4:]}/{event.message.id}"
                    caption_text += original_link
                
                # 添加时间信息
                if rule.is_original_time and context.time_info:
                    caption_text += context.time_info
                
                # 设置默认描述（如果没有文本内容）
                default_caption = f"收到一组媒体文件 (共{len(files)}个)"
                
                # 按配置的媒体发送方式分别处理每个推送配置
                processed_files = []
                
                for config in push_configs:
                    # 获取该配置的媒体发送模式
                    send_mode = config.media_send_mode  # "Single" 或 "Multiple"
                    
                    # 检查所有文件是否存在
                    valid_files = [f for f in files if os.path.exists(str(f))]
                    if not valid_files:
                        continue
                    
                    # 根据媒体发送模式来决定发送方式
                    if send_mode == "Multiple":
                        try:
                            logger.info(f'尝试一次性发送 {len(valid_files)} 个文件到 {config.push_channel}，模式: {send_mode}')
                            await self._send_push_notification(
                                [config], 
                                caption_text or f"收到一组媒体文件 (共{len(valid_files)}个)", 
                                None,  # 不使用单附件参数
                                valid_files,  # 使用多附件参数
                                context=context
                            )
                            processed_files.extend(valid_files)
                        except Exception as e:
                            logger.error(f'尝试一次性发送多个文件失败，错误: {str(e)}')
                            # 如果一次性发送失败，则尝试逐个发送
                            for i, file_path in enumerate(valid_files):
                                # 第一个文件使用完整文本，后续文件使用简短描述
                                file_caption = caption_text if i == 0 else f"媒体组的第 {i+1} 个文件"
                                await self._send_push_notification([config], file_caption, file_path, context=context)
                                processed_files.append(file_path)
                    # 逐个发送文件
                    else:
                        for i, file_path in enumerate(valid_files):
                            # 第一个文件使用完整文本，后续文件使用简短描述
                            if i == 0:
                                file_caption = caption_text or f"收到一组媒体文件 (共{len(valid_files)}个)"
                            else:
                                file_caption = f"媒体组的第 {i+1} 个文件" if len(valid_files) > 1 else ""
                            
                            await self._send_push_notification([config], file_caption, file_path, context=context)
                            processed_files.append(file_path)
                
        except Exception as e:
            logger.error(f'推送媒体组消息时出错: {str(e)}')
            logger.error(traceback.format_exc())
            raise
        finally:
            # 如果是自己下载的文件，立即清理
            if need_cleanup:
                for file_path in files:
                    try:
                        if os.path.exists(str(file_path)):
                            os.remove(file_path)
                            logger.info(f'删除临时文件: {file_path}')
                            # 移除已删除的文件，避免重复删除
                            if file_path in processed_files:
                                processed_files.remove(file_path)
                    except Exception as e:
                        logger.error(f'删除临时文件失败: {str(e)}')
            
            # 返回处理过但未删除的文件
            return processed_files
    
    async def _push_single_media(self, context, push_configs):
        """推送单条媒体消息"""
        rule = context.rule
        client = context.client
        event = context.event
        
        logger.info(f'推送单条媒体消息')
        
        # 初始化处理文件列表
        processed_files = []
        
        # 检查是否所有媒体都超限
        if context.skipped_media and not context.media_files:
            # 构建提示信息
            file_size = context.skipped_media[0][1]
            file_name = context.skipped_media[0][2]
            
            text_to_send = context.message_text or ''
            text_to_send += f"\n\n⚠️ 媒体文件 {file_name} ({file_size}MB) 超过大小限制"
            
            # 添加发送者信息
            if rule.is_original_sender:
                text_to_send = context.sender_info + text_to_send
            
            # 添加时间信息
            if rule.is_original_time:
                text_to_send += context.time_info
            
            # 添加原始链接
            if rule.is_original_link:
                original_link = f"\n原始消息: https://t.me/c/{str(event.chat_id)[4:]}/{event.message.id}"
                text_to_send += original_link
            
            # 发送文本推送
            await self._send_push_notification(push_configs, text_to_send, context=context)
            return processed_files
        
        # 处理媒体文件
        files = []
        need_cleanup = False
        
        try:
            # 如果SenderFilter已经下载了文件，使用它们
            if context.media_files:
                logger.info(f'使用SenderFilter已下载的文件: {len(context.media_files)}个')
                files = context.media_files
            # 否则，需要自己下载文件
            elif rule.enable_only_push and event.message and event.message.media:
                logger.info(f'需要自己下载文件，开始下载单个媒体消息...')
                need_cleanup = True
                file_path = await event.message.download_media(os.path.join(os.getcwd(), 'temp'))
                if file_path:
                    files.append(file_path)
                    logger.info(f'已下载媒体文件: {file_path}')
            
            # 发送媒体文件
            for file_path in files:
                try:
                    # 构建推送内容
                    caption = ""
                    if rule.is_original_sender and context.sender_info:
                        caption += context.sender_info
                    caption += context.message_text or ""
                    
                    # 添加时间信息
                    if rule.is_original_time and context.time_info:
                        caption += context.time_info
                    
                    # 添加原始链接
                    if rule.is_original_link and context.original_link:
                        caption += context.original_link
                    
                    # 如果没有文本内容，添加默认描述
                    if not caption:
                        # 根据文件类型设置描述
                        caption = " "
                        # ext = os.path.splitext(str(file_path))[1].lower()
                        # if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                        #     caption = "收到一张图片"
                        # elif ext in ['.mp4', '.avi', '.mkv', '.mov', '.webm']:
                        #     caption = "收到一个视频"
                        # elif ext in ['.mp3', '.wav', '.ogg', '.flac']:
                        #     caption = "收到一个音频文件"
                        # else:
                        #     caption = f"收到一个文件 ({ext})"
                    
                    # 发送推送
                    await self._send_push_notification(push_configs, caption, file_path, context=context)
                    # 添加到已处理文件列表
                    processed_files.append(file_path)
                    
                except Exception as e:
                    logger.error(f'推送单个媒体文件时出错: {str(e)}')
                    logger.error(traceback.format_exc())
                    raise
                
        except Exception as e:
            logger.error(f'推送单条媒体消息时出错: {str(e)}')
            logger.error(traceback.format_exc())
            raise
        finally:
            # 如果是自己下载的文件，需要清理
            if need_cleanup:
                for file_path in files:
                    try:
                        if os.path.exists(str(file_path)):
                            os.remove(file_path)
                            logger.info(f'删除临时文件: {file_path}')
                            # 从已处理列表中移除
                            if file_path in processed_files:
                                processed_files.remove(file_path)
                    except Exception as e:
                        logger.error(f'删除临时文件失败: {str(e)}')
    
            # 返回处理过但未删除的文件
            return processed_files
    
    async def _push_text_message(self, context, push_configs):
        """推送纯文本消息"""
        rule = context.rule
        
        if not context.message_text:
            logger.info('没有文本内容，不发送推送')
            return []
        
        # 组合消息文本
        message_text = ""
        if rule.is_original_sender and context.sender_info:
            message_text += context.sender_info
        message_text += context.message_text
        if rule.is_original_time and context.time_info:
            message_text += context.time_info
        if rule.is_original_link and context.original_link:
            message_text += context.original_link
        
        # 发送推送
        await self._send_push_notification(push_configs, message_text, context=context)
        logger.info(f'文本消息推送已发送')
        
        # 返回空列表，表示没有处理任何文件
        return []
    
    async def _build_api_payload(self, context, body):
        event = context.event
        rule = context.rule
        source_chat_id = safe_display_name(getattr(event, "chat_id", ""))
        source_name = resolve_chat_name(context)
        sender_meta = resolve_sender_metadata(event)
        original_link = build_original_link(getattr(event, "chat_id", ""), getattr(event.message, "id", ""))
        raw_message = context.original_message_text or context.message_text or body or ""
        processed_message = context.message_text or ""
        reply_source_message_id = safe_display_name(getattr(event.message, "reply_to_msg_id", ""))
        reply_preview_text = safe_display_name(getattr(context, "reply_text", ""))
        reply_has_media = False

        if reply_source_message_id and not reply_preview_text:
            try:
                reply_message = await event.message.get_reply_message()
            except Exception:
                reply_message = None
            if reply_message is not None:
                reply_preview_text = safe_display_name(getattr(reply_message, "text", "")) or safe_display_name(getattr(reply_message, "message", ""))
                reply_has_media = bool(getattr(reply_message, "media", None))

        payload = {
            "version": "1.0",
            "type": "info",
            "title": "",
            "message": raw_message,
            "processed_message": processed_message,
            "delivery_text": body or "",
            "source_type": "telegram",
            "source_name": source_name,
            "source_channel": source_name,
            "source_chat_id": source_chat_id,
            "source_message_id": safe_display_name(getattr(event.message, "id", "")),
            "sender_name": sender_meta["sender_name"],
            "sender_id": sender_meta["sender_id"],
            "sender_type": sender_meta["sender_type"],
            "rule_id": getattr(rule, "id", None),
            "collected_at": datetime.utcnow().isoformat() + "Z",
            "original_link": original_link,
            "has_media": bool(context.media_files or getattr(event.message, "media", None)),
            "is_reply_message": bool(reply_source_message_id),
            "reply_to_source_message_id": reply_source_message_id,
            "reply_preview_text": reply_preview_text,
            "reply_has_media": reply_has_media,
            "reply_matched_forward": bool(getattr(context, "reply_matched_forward", False)),
            "attachments": []
        }

        if getattr(rule, "source_chat", None) is not None:
            payload["source_chat_db_id"] = getattr(rule.source_chat, "id", None)
            payload["source_chat_db_name"] = safe_display_name(getattr(rule.source_chat, "name", ""))

        return payload

    async def _send_api_push_notification(self, service_url, context, body, attachment=None, all_attachments=None):
        url = normalize_api_push_url(service_url)
        payload = await self._build_api_payload(context, body)
        attachments = [path for path in (all_attachments or []) if path and os.path.exists(str(path))]
        if attachment and os.path.exists(str(attachment)):
            attachments.append(attachment)

        timeout = aiohttp.ClientTimeout(total=120)
        file_handles = []
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if attachments:
                    form = aiohttp.FormData()
                    for key, value in payload.items():
                        if value is None:
                            continue
                        if isinstance(value, (dict, list)):
                            form.add_field(key, json.dumps(value, ensure_ascii=False))
                        else:
                            form.add_field(key, str(value))
                    for index, file_path in enumerate(attachments, start=1):
                        file_handle = open(file_path, "rb")
                        file_handles.append(file_handle)
                        form.add_field(
                            f"file{index:02d}",
                            file_handle,
                            filename=os.path.basename(str(file_path)),
                            content_type="application/octet-stream"
                        )
                    response = await session.post(url, data=form)
                else:
                    response = await session.post(url, json=payload)

                response_text = await response.text()
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"API推送失败({response.status}): {response_text[:1000]}")

                logger.info(f'API推送发送成功: {url} -> {response.status}')
                if response_text.strip():
                    logger.info(f'API推送响应: {response_text[:500]}')
        finally:
            for file_handle in file_handles:
                try:
                    file_handle.close()
                except Exception:
                    pass

    async def _send_push_notification(self, push_configs, body, attachment=None, all_attachments=None, context=None):
        """发送推送通知"""
        if not body and not attachment and not all_attachments:
            logger.warning('没有内容可推送')
            return
        
        for config in push_configs:
            try:
                service_url = config.push_channel
                if is_api_push_channel(service_url):
                    logger.info(f'使用结构化API推送: {service_url}')
                    await self._send_api_push_notification(
                        service_url,
                        context,
                        body,
                        attachment,
                        all_attachments
                    )
                    continue

                # 创建Apprise对象
                apobj = apprise.Apprise()
                
                # 添加推送服务
                if apobj.add(service_url):
                    logger.info(f'成功添加推送服务: {service_url}')
                else:
                    logger.error(f'添加推送服务失败: {service_url}')
                    continue
                
                # 发送推送
                if all_attachments and len(all_attachments) > 0 and config.media_send_mode == "Multiple":
                    # 尝试一次性发送所有附件
                    logger.info(f'发送带{len(all_attachments)}个附件的推送，模式: {config.media_send_mode}')
                    send_result = await asyncio.to_thread(
                        apobj.notify,
                        body=body or f"收到{len(all_attachments)}个媒体文件",
                        attach=all_attachments
                    )
                elif attachment and os.path.exists(str(attachment)):
                    # 单附件推送
                    logger.info(f'发送带单个附件的推送: {os.path.basename(str(attachment))}')
                    send_result = await asyncio.to_thread(
                        apobj.notify,
                        body=body or " ",
                        attach=attachment
                    )
                else:
                    # 纯文本推送
                    logger.info('发送纯文本推送')
                    send_result = await asyncio.to_thread(
                        apobj.notify,
                        body=body
                    )
                
                if send_result:
                    logger.info(f'推送发送成功: {service_url}')
                else:
                    logger.error(f'推送发送失败: {service_url}')
                
            except Exception as e:
                logger.error(f'发送推送时出错: {str(e)}')
                logger.error(traceback.format_exc())
