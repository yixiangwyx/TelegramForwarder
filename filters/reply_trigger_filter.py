import logging
from filters.base_filter import BaseFilter
from filters.keyword_filter import KeywordFilter
from utils.common import check_keywords, get_main_module
from models.models import get_session, ForwardedMessageMap
import asyncio

logger = logging.getLogger(__name__)

class ReplyTriggerFilter(BaseFilter):
    """
    引用触发过滤器：当消息为引用消息，且引用目标已转发过时，
    强制允许继续处理，并可选择跳过后续关键字检查。
    并为此类消息缓存 reply->已转发目标 的映射，供发送阶段使用。
    """
    async def _process(self, context):
        rule = context.rule
        event = context.event
        message = event.message
        if not message:
            return True
        if not getattr(message, 'reply_to_msg_id', None):
            return True
        if not getattr(rule, 'enable_reply_forward', False):
            return True

        source_chat_id = str(event.chat_id)
        source_msg_id = message.reply_to_msg_id
        target_chat_id = str(rule.target_chat.telegram_chat_id)
        reply_target_msg_id = None

        try:
            # 主会话
            source_client = context.client

            # 获取源消息引用目标
            try:
                src_chat_entity = await source_client.get_entity(int(source_chat_id))
            except Exception:
                src_chat_entity = None
            try:
                replied_message = await source_client.get_messages(src_chat_entity or int(source_chat_id), ids=int(source_msg_id))
            except Exception:
                replied_message = None
            if replied_message:
                replied_text = replied_message.text or ''
            else:
                replied_text = ''

            # 检查是否已转发过
            session = get_session()
            try:
                mapping = session.query(ForwardedMessageMap).filter(
                    ForwardedMessageMap.rule_id == rule.id,
                    ForwardedMessageMap.source_chat_telegram_id == source_chat_id,
                    ForwardedMessageMap.source_message_id == int(source_msg_id),
                ).first()
                if mapping:
                    reply_target_msg_id = mapping.target_message_id

                # 即使没找到映射，也允许这条消息继续处理，不阻断
                context.should_forward = True
                # 缓存映射关系，供发送阶段使用
                context.reply_source_id = int(source_msg_id)
                context.reply_target_id = reply_target_msg_id
                context.reply_text = replied_text

                # 按策略决定是否跳过关键字/AI/AI后关键字
                if getattr(rule, 'reply_forward_ai_check', True):
                    logger.info('引用消息进入正常过滤链路（保守模式）')
                    return True

                # 激进模式：跳过 AI 过滤
                logger.info('引用消息启用激进模式：跳过 AI 过滤')
                # 将AI过滤器的结果强制为通过，后续避免再次被关键字检查阻断
                # 标记上下文，供发送/后续感知
                context.force_forward_by_reply = True
                return True
            finally:
                session.close()
        except Exception as e:
            logger.error(f'ReplyTriggerFilter处理出错: {e}')
            return True
