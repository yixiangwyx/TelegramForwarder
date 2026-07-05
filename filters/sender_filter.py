import logging
import os

from telethon.errors import FloodWaitError

from enums.enums import PreviewMode
from filters.base_filter import BaseFilter
from utils.common import record_forwarded_message, resolve_reply_target

logger = logging.getLogger(__name__)


class SenderFilter(BaseFilter):
    """Send the processed message to the target chat."""

    async def _process(self, context):
        rule = context.rule
        client = context.client
        event = context.event
        target_chat = rule.target_chat
        target_chat_id = int(target_chat.telegram_chat_id)

        if not context.should_forward:
            logger.info("Message does not satisfy forwarding conditions, skip send")
            return False

        if rule.enable_only_push:
            logger.info("Rule is push-only, skip Telegram send")
            return True

        try:
            target_chat_id = await self._resolve_target_chat_id(client, target_chat, target_chat_id)
        except Exception as exc:
            logger.warning(f"Failed to resolve target chat entity: {exc}")

        parse_mode = rule.message_mode.value
        logger.info(f"Using parse mode: {parse_mode}")

        try:
            reply_to_target_id = None
            if getattr(rule, "enable_reply_forward", False) and getattr(event.message, "reply_to_msg_id", None):
                reply_to_target_id = await resolve_reply_target(
                    rule,
                    event.chat_id,
                    event.message.reply_to_msg_id,
                )

            if context.is_media_group or (context.media_group_messages and context.skipped_media):
                logger.info("Sending media group message")
                await self._send_media_group(context, target_chat_id, parse_mode, reply_to=reply_to_target_id)
            elif context.media_files or context.skipped_media:
                logger.info("Sending single media message")
                await self._send_single_media(context, target_chat_id, parse_mode, reply_to=reply_to_target_id)
            else:
                logger.info("Sending text message")
                await self._send_text_message(context, target_chat_id, parse_mode, reply_to=reply_to_target_id)

            try:
                target_msg_id = None
                if getattr(context, "forwarded_messages", None):
                    target_msg_id = context.forwarded_messages[-1].id
                if target_msg_id is None:
                    target_msg_id = getattr(context, "last_sent_message_id", None)
                if target_msg_id:
                    await record_forwarded_message(
                        rule.id,
                        event.chat_id,
                        event.message.id,
                        target_chat_id,
                        target_msg_id,
                    )
            except Exception as exc:
                logger.error(f"Failed to record forwarded message map: {exc}")

            logger.info(f"Message sent to {target_chat.name} ({target_chat_id})")
            return True
        except FloodWaitError as exc:
            wait_time = exc.seconds
            logger.error(f"Telegram rate limit hit, wait {wait_time} seconds")
            context.errors.append(f"Telegram rate limit hit, wait {wait_time} seconds")
            return False
        except Exception as exc:
            logger.error(f"Failed to send message: {exc}")
            context.errors.append(f"Failed to send message: {exc}")
            return False

    async def _resolve_target_chat_id(self, client, target_chat, target_chat_id):
        try:
            await client.get_entity(target_chat_id)
            logger.info(f"Resolved target chat entity: {target_chat.name} (ID: {target_chat_id})")
            return target_chat_id
        except Exception as first_error:
            try:
                if not str(target_chat_id).startswith("-100"):
                    super_group_id = int(f"-100{abs(target_chat_id)}")
                    await client.get_entity(super_group_id)
                    logger.info(f"Resolved target chat via -100 prefix: {target_chat.name} (ID: {super_group_id})")
                    return super_group_id
            except Exception as second_error:
                try:
                    if not str(target_chat_id).startswith("-"):
                        group_id = int(f"-{abs(target_chat_id)}")
                        await client.get_entity(group_id)
                        logger.info(f"Resolved target chat via - prefix: {target_chat.name} (ID: {group_id})")
                        return group_id
                except Exception as third_error:
                    logger.warning(
                        "Unable to resolve target chat entity, continue with original id: "
                        f"{first_error}, {second_error}, {third_error}"
                    )
        return target_chat_id

    async def _send_media_group(self, context, target_chat_id, parse_mode, reply_to=None):
        rule = context.rule
        client = context.client
        event = context.event

        context.forwarded_messages = []
        context.last_sent_message_id = None

        files = []
        try:
            for message in context.media_group_messages:
                if message.media:
                    file_path = await message.download_media(os.path.join(os.getcwd(), "temp"))
                    if file_path:
                        files.append(file_path)

            if not files:
                return

            if not hasattr(context, "media_files") or context.media_files is None:
                context.media_files = []
            context.media_files.extend(files)

            caption_text = (context.sender_info or "") + (context.message_text or "")
            for _, size, name in context.skipped_media:
                display_name = name or "unnamed"
                caption_text += f"\n\nWarning: media file {display_name} ({size}MB) exceeds size limit"

            if context.skipped_media:
                context.original_link = f"\nOriginal message: https://t.me/c/{str(event.chat_id)[4:]}/{event.message.id}"
            caption_text += (context.time_info or "") + (context.original_link or "")

            send_kwargs = {
                "files": files,
                "caption": caption_text,
                "parse_mode": parse_mode,
                "buttons": context.buttons,
                "link_preview": self._get_link_preview(rule, context),
            }
            if reply_to:
                send_kwargs["reply_to"] = reply_to

            sent_messages = await client.send_file(target_chat_id, **send_kwargs)
            if isinstance(sent_messages, list):
                context.forwarded_messages = sent_messages
            else:
                context.forwarded_messages = [sent_messages]
            if context.forwarded_messages:
                context.last_sent_message_id = context.forwarded_messages[-1].id

            logger.info(f"Media group sent, stored {len(context.forwarded_messages)} sent messages")
        except Exception as exc:
            logger.error(f"Failed to send media group: {exc}")
            raise
        finally:
            if not rule.enable_push:
                for file_path in files:
                    try:
                        os.remove(file_path)
                    except Exception as exc:
                        logger.error(f"Failed to remove temp file {file_path}: {exc}")

    async def _send_single_media(self, context, target_chat_id, parse_mode, reply_to=None):
        rule = context.rule
        client = context.client
        event = context.event

        logger.info("Sending single media")

        if context.skipped_media and not context.media_files:
            file_size = context.skipped_media[0][1]
            file_name = context.skipped_media[0][2]
            original_link = f"\nOriginal message: https://t.me/c/{str(event.chat_id)[4:]}/{event.message.id}"

            text_to_send = context.message_text or ""
            text_to_send += f"\n\nWarning: media file {file_name} ({file_size}MB) exceeds size limit"
            text_to_send = (context.sender_info or "") + text_to_send + (context.time_info or "")
            text_to_send += original_link

            sent_message = await client.send_message(
                target_chat_id,
                text_to_send,
                parse_mode=parse_mode,
                link_preview=True,
                buttons=context.buttons,
                reply_to=reply_to,
            )
            context.forwarded_messages = [sent_message]
            context.last_sent_message_id = sent_message.id
            logger.info("Media exceeded size limit, sent text only")
            return

        if not hasattr(context, "media_files") or context.media_files is None:
            context.media_files = []

        context.forwarded_messages = []
        context.last_sent_message_id = None
        for file_path in context.media_files:
            try:
                caption = (
                    (context.sender_info or "")
                    + (context.message_text or "")
                    + (context.time_info or "")
                    + (context.original_link or "")
                )

                send_kwargs = {
                    "file": file_path,
                    "caption": caption,
                    "parse_mode": parse_mode,
                    "buttons": context.buttons,
                    "link_preview": self._get_link_preview(rule, context),
                }
                if reply_to:
                    send_kwargs["reply_to"] = reply_to

                sent_message = await client.send_file(target_chat_id, **send_kwargs)
                context.forwarded_messages.append(sent_message)
                context.last_sent_message_id = sent_message.id
                logger.info("Single media message sent")
            except Exception as exc:
                logger.error(f"Failed to send media message: {exc}")
                raise
            finally:
                if not rule.enable_push:
                    try:
                        os.remove(file_path)
                    except Exception as exc:
                        logger.error(f"Failed to remove temp file {file_path}: {exc}")

    async def _send_text_message(self, context, target_chat_id, parse_mode, reply_to=None):
        rule = context.rule
        client = context.client

        if not context.message_text:
            logger.info("No text content to send")
            return

        message_text = (
            (context.sender_info or "")
            + (context.message_text or "")
            + (context.time_info or "")
            + (context.original_link or "")
        )

        send_kwargs = {
            "message": str(message_text),
            "parse_mode": parse_mode,
            "link_preview": self._get_link_preview(rule, context),
            "buttons": context.buttons,
        }
        if reply_to:
            send_kwargs["reply_to"] = reply_to

        sent_message = await client.send_message(target_chat_id, **send_kwargs)
        context.forwarded_messages = [sent_message]
        context.last_sent_message_id = sent_message.id
        logger.info("Text message sent")

    def _get_link_preview(self, rule, context):
        return {
            PreviewMode.ON: True,
            PreviewMode.OFF: False,
            PreviewMode.FOLLOW: context.event.message.media is not None,
        }[rule.is_preview]
