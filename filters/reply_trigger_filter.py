import asyncio
import logging

from filters.base_filter import BaseFilter
from models.models import ForwardedMessageMap, get_session

logger = logging.getLogger(__name__)

REPLY_MAPPING_RETRY_DELAYS = (0, 0.25, 0.75, 1.5)
MAX_REPLY_ANCESTOR_DEPTH = 10

class ReplyTriggerFilter(BaseFilter):
    """
    引用触发过滤器：当消息为引用消息，且引用目标已转发过时，
    跳过关键词过滤，并可选择跳过 AI 过滤。
    同时缓存 reply->已转发目标 的映射，供发送阶段使用。
    """
    @staticmethod
    def _find_mapping(rule_id, source_chat_id, source_msg_id):
        session = get_session()
        try:
            return session.query(ForwardedMessageMap).filter(
                ForwardedMessageMap.rule_id == rule_id,
                ForwardedMessageMap.source_chat_telegram_id == source_chat_id,
                ForwardedMessageMap.source_message_id == int(source_msg_id),
            ).first()
        finally:
            session.close()

    @staticmethod
    async def _get_source_message(context, source_msg_id):
        message = context.event.message
        if int(getattr(message, "reply_to_msg_id", 0) or 0) == int(source_msg_id):
            try:
                return await message.get_reply_message()
            except Exception as exc:
                logger.warning("读取直接引用消息失败 source_msg_id=%s: %s", source_msg_id, exc)

        client = getattr(context.event, "client", None) or getattr(context, "client", None)
        if client is None or not hasattr(client, "get_messages"):
            return None
        try:
            return await client.get_messages(context.event.chat_id, ids=int(source_msg_id))
        except Exception as exc:
            logger.warning("读取引用祖先消息失败 source_msg_id=%s: %s", source_msg_id, exc)
            return None

    async def _resolve_forwarded_ancestor(self, context, source_chat_id, direct_source_msg_id):
        """Resolve the nearest forwarded message in a Telegram reply chain.

        A short retry window lets a concurrently processed root message finish
        sending and persist its mapping before its immediate reply is filtered.
        """
        for attempt, delay in enumerate(REPLY_MAPPING_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)

            source_msg_id = int(direct_source_msg_id)
            visited = set()
            for depth in range(MAX_REPLY_ANCESTOR_DEPTH):
                if source_msg_id in visited:
                    break
                visited.add(source_msg_id)

                mapping = self._find_mapping(context.rule.id, source_chat_id, source_msg_id)
                if mapping:
                    return mapping, source_msg_id, depth

                source_message = await self._get_source_message(context, source_msg_id)
                parent_source_msg_id = int(getattr(source_message, "reply_to_msg_id", 0) or 0)
                if not parent_source_msg_id:
                    break
                source_msg_id = parent_source_msg_id

            if attempt < len(REPLY_MAPPING_RETRY_DELAYS) - 1:
                logger.info(
                    "引用映射暂未命中，等待并发根消息完成转发: source_msg_id=%s, attempt=%s",
                    direct_source_msg_id,
                    attempt + 1,
                )

        return None, None, None

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
        direct_source_msg_id = int(message.reply_to_msg_id)

        try:
            mapping, matched_source_msg_id, ancestor_depth = await self._resolve_forwarded_ancestor(
                context,
                source_chat_id,
                direct_source_msg_id,
            )
            if not mapping:
                logger.info(
                    "引用消息及其祖先均未命中已转发映射，继续正常过滤链路: source_msg_id=%s",
                    direct_source_msg_id,
                )
                return True

            context.should_forward = True
            # Link downstream consumers to the mapped ancestor, not to an
            # unforwarded intermediate reply that they cannot resolve.
            context.reply_source_id = int(matched_source_msg_id)
            context.reply_target_id = mapping.target_message_id
            context.reply_matched_forward = True
            context.skip_keyword_filter = True

            if ancestor_depth:
                logger.info(
                    "引用消息通过祖先链命中已转发映射: direct_source_msg_id=%s, "
                    "matched_source_msg_id=%s, depth=%s, target_msg_id=%s",
                    direct_source_msg_id,
                    matched_source_msg_id,
                    ancestor_depth,
                    mapping.target_message_id,
                )

            if getattr(rule, "reply_forward_ai_check", True):
                logger.info(
                    "引用消息命中已转发映射，跳过关键词过滤并继续 AI 处理: "
                    f"source_msg_id={matched_source_msg_id}, target_msg_id={mapping.target_message_id}"
                )
            else:
                context.skip_ai_filter = True
                logger.info(
                    "引用消息命中已转发映射，跳过关键词和 AI 过滤: "
                    f"source_msg_id={matched_source_msg_id}, target_msg_id={mapping.target_message_id}"
                )

            return True
        except Exception as e:
            logger.error(f'ReplyTriggerFilter处理出错: {e}')
            return True
