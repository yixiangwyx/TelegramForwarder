import logging

from filters.base_filter import BaseFilter
from models.models import ForwardedMessageMap, get_session

logger = logging.getLogger(__name__)

class ReplyTriggerFilter(BaseFilter):
    """
    引用触发过滤器：当消息为引用消息，且引用目标已转发过时，
    跳过关键词过滤，并可选择跳过 AI 过滤。
    同时缓存 reply->已转发目标 的映射，供发送阶段使用。
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

        try:
            session = get_session()
            try:
                mapping = session.query(ForwardedMessageMap).filter(
                    ForwardedMessageMap.rule_id == rule.id,
                    ForwardedMessageMap.source_chat_telegram_id == source_chat_id,
                    ForwardedMessageMap.source_message_id == int(source_msg_id),
                ).first()

                if not mapping:
                    logger.info("引用消息未命中已转发映射，继续正常过滤链路")
                    return True

                context.should_forward = True
                context.reply_source_id = int(source_msg_id)
                context.reply_target_id = mapping.target_message_id
                context.reply_matched_forward = True
                context.skip_keyword_filter = True

                if getattr(rule, "reply_forward_ai_check", True):
                    logger.info(
                        "引用消息命中已转发映射，跳过关键词过滤并继续 AI 处理: "
                        f"source_msg_id={source_msg_id}, target_msg_id={mapping.target_message_id}"
                    )
                else:
                    context.skip_ai_filter = True
                    logger.info(
                        "引用消息命中已转发映射，跳过关键词和 AI 过滤: "
                        f"source_msg_id={source_msg_id}, target_msg_id={mapping.target_message_id}"
                    )

                return True
            finally:
                session.close()
        except Exception as e:
            logger.error(f'ReplyTriggerFilter处理出错: {e}')
            return True
