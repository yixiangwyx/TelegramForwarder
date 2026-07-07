![img](images/logo/png/logo-title.png)

<h3><div align="center">Telegram 转发器 | Telegram Forwarder</div></h3>

---

## 简介

TelegramForwarder 是一个基于 `Telethon + Telegram Bot + SQLite` 的消息转发与处理工具。
它使用你的用户账号监听频道/群组消息，再按规则转发、编辑、推送或生成 RSS，不要求 Bot 先加入源频道，因此适合做信息流聚合、内容清洗、提醒分发和素材整理。

当前仓库已经不只是“关键词转发器”，而是一个带完整过滤链的消息处理中枢，实际包含这些能力：

- 多源绑定，把多个源频道/群组汇总到同一个目标聊天
- 黑白名单、正则、替换、延迟处理、媒体过滤
- AI 改写、AI 去重、AI 总结
- 引用转发，并可控制“引用消息是否继续走 AI”
- 定时发布消息到目标聊天
- Apprise 多平台推送
- RSS 订阅与 Web 管理页
- 单图自动裁剪，支持固定裁剪、按比例裁剪、底部二维码识别裁剪
- UFB 联动能力

## 项目结构

核心入口和模块：

- `main.py`：启动用户客户端、Bot 客户端、总结调度器、定时发布调度器、聊天信息更新器
- `message_listener.py`：Telegram 消息监听入口
- `filters/process.py`：过滤链装配
- `handlers/`：Bot 命令、按钮回调、文本输入流程
- `scheduler/`：AI 总结与定时发布
- `rss/`：RSS 服务与 Web 管理页
- `utils/image_cropper.py`：图片裁剪能力
- `scripts/test_image_crop.py`：本地验证裁剪效果

## 功能概览

### 过滤链

当前代码里的处理顺序如下：

`Init -> Delay -> ReplyTrigger -> Keyword -> Replace -> Media -> AI -> Info -> CommentButton -> RSS -> Edit -> Sender -> Reply -> Push -> DeleteOriginal`

其中几项和最近功能最相关：

- `ReplyTriggerFilter`：命中已转发映射时，引用消息可跳过关键词过滤，并按规则决定是否跳过 AI
- `MediaFilter` / `SenderFilter`：单图会在发送前尝试自动裁剪
- `RSSFilter`：启用 RSS 且规则开启“只转发到 RSS”时，会在这里终止后续转发
- `PushFilter`：使用 Apprise 将消息继续分发到第三方平台

### 规则管理入口

项目当前有两套管理入口：

- Bot 端：`/settings`、按钮菜单、文本输入回填
- Web 端：启用 RSS 服务后可访问管理页，除了 RSS 配置，也能编辑规则的 AI 总结、引用转发、定时发布等字段

如果你主要用 Bot 管理规则，常用命令是：

- `/bind`：绑定源聊天和目标聊天
- `/switch`：切换当前聊天下“命令操作作用到哪条规则”
- `/settings`：打开规则菜单

## 快速开始

### 1. 准备 Telegram 凭据

必填环境变量在 [`.env.example`](./.env.example) 中已经列好：

- `API_ID` / `API_HASH`
- `PHONE_NUMBER`
- `BOT_TOKEN`
- `USER_ID`

获取方式：

1. `API_ID` 和 `API_HASH`：访问 `https://my.telegram.org/apps`
2. `BOT_TOKEN`：联系 `@BotFather`
3. `USER_ID`：联系 `@userinfobot`

### 2. 准备配置文件

```bash
git clone git@github.com:yixiangwyx/TelegramForwarder.git
cd TelegramForwarder
cp .env.example .env
```

按需修改 `.env` 后即可启动。

### 3. 首次登录

首次需要让用户账号完成 Telegram 登录验证，推荐直接运行：

```bash
docker compose run --rm telegram-forwarder
```

按提示完成手机号、验证码或二次验证后退出。

### 4. 后台运行

```bash
docker compose up -d
```

查看日志：

```bash
docker compose logs -f telegram-forwarder
```

### 5. 更新

```bash
docker compose down
git pull
docker compose up -d --build
```

## 基础使用流程

### 1. 绑定规则

在目标群组、频道或私聊里发送：

```bash
/bind https://t.me/tgnews
```

也可以同时指定目标聊天：

```bash
/bind https://t.me/source_channel https://t.me/target_channel
```

### 2. 打开设置

```bash
/settings
```

如果当前聊天下绑定了多条规则，可以先切换“当前操作规则”：

```bash
/switch
```

### 3. 添加过滤规则

```bash
/add 广告 推广
/add_regex BTC.*
/replace \\*\\*
```

如果要把操作同步到当前聊天绑定的所有规则，可用：

```bash
/add_all 关键字
/replace_all 原内容 新内容
```

## 重要功能说明

### 引用转发

项目当前已经支持“引用消息保持引用关系继续转发”。

相关规则项：

- `↩️ 引用转发`
- `🧠 引用走AI`

实际行为：

- 新消息如果回复的是一条“之前已经转发过”的源消息，会命中转发映射
- 命中后会跳过关键词过滤
- 如果 `引用走AI` 关闭，则也会跳过 AI 处理
- 最终目标聊天中会尽量保留引用关系

### 定时发布

项目当前已经内置定时发布调度器，Bot 菜单中可直接管理。

支持三种触发方式：

- 每天固定时间
- 每隔若干小时
- 每隔若干分钟

Web 管理页中也可以编辑 `scheduled_message_configs`，调度器会定期对账并自动加载。

### AI 总结

AI 总结调度器会按规则的 `summary_time` 运行，并把过去一段时间的消息汇总发送到目标聊天。

相关环境变量：

- `DEFAULT_AI_MODEL`
- `DEFAULT_SUMMARY_PROMPT`
- `DEFAULT_SUMMARY_TIME`
- `SUMMARY_BATCH_SIZE`
- `SUMMARY_BATCH_DELAY`

自定义可选时间列表文件：

- `config/summary_times.txt`

### 图片自动裁剪

仓库当前已经内置非大模型图片裁剪能力，默认通过 `.env` 控制。

常用环境变量：

```ini
ENABLE_IMAGE_CROP=true
IMAGE_CROP_TOP_PX=
IMAGE_CROP_BOTTOM_PX=
IMAGE_CROP_LEFT_PX=
IMAGE_CROP_RIGHT_PX=
IMAGE_CROP_TOP_RATIO=
IMAGE_CROP_BOTTOM_RATIO=
IMAGE_CROP_LEFT_RATIO=
IMAGE_CROP_RIGHT_RATIO=
IMAGE_CROP_AUTO_BOTTOM_QR=true
IMAGE_CROP_TOP_RATIO_IF_QR=0.1475
IMAGE_CROP_PRESERVE_SQUARE=false
IMAGE_CROP_JPEG_QUALITY=95
```

说明：

- 固定像素和比例裁剪可以混用，像素优先
- 可先检测图片底部二维码，再自动裁掉底部区域
- 命中二维码后还能额外对顶部做二次裁剪
- 可选“补边保持方图”

本地测试脚本：

```bash
python scripts/test_image_crop.py input.jpg output.jpg --top-ratio 0.12 --auto-bottom-qr
```

### 推送能力

项目通过 `Apprise` 做第三方分发，适合继续推送到：

- ntfy
- Telegram
- 邮件
- Webhook / API
- 其他 Apprise 支持的平台

推送设置界面支持：

- 是否只走推送配置
- 媒体逐条发送或合并发送

### RSS 与 Web 管理页

启用 RSS：

```ini
RSS_ENABLED=true
RSS_BASE_URL=
RSS_MEDIA_BASE_URL=
```

当前 `docker-compose.yml` 已映射：

- `9804:8000`

启动后访问：

```text
http://你的服务器地址:9804/
```

Web 端当前不仅能维护 RSS 配置，也能查看和编辑规则的部分高级字段，例如：

- AI 总结
- 引用转发
- 引用走 AI
- 定时发布列表

### UFB 联动

如需与通用论坛屏蔽插件服务端联动，可启用：

```ini
UFB_ENABLED=true
UFB_SERVER_URL=
UFB_TOKEN=
```

对应命令：

- `/ufb_bind`
- `/ufb_unbind`
- `/ufb_item_change`

## 配置文件补充说明

项目会自动在 `config/` 目录下创建一些可维护的列表文件：

- `config/ai_models.json`
- `config/summary_times.txt`
- `config/delay_times.txt`
- `config/max_media_size.txt`
- `config/media_extensions.txt`

这些文件名是当前代码实际读取的名字。旧文档里常见的 `summary_time.txt`、`delay_time.txt`、`media_size.txt` 已经不准确。

## 常用命令

### 基础命令

```bash
/start
/help
/changelog
```

### 规则管理

```bash
/bind(/b) <源聊天链接或名称> [目标聊天链接或名称]
/settings(/s) [规则ID]
/switch(/sw)
/list_rule(/lr)
/copy_rule(/cr) <源规则ID> [目标规则ID]
/delete_rule(/dr) <规则ID> [规则ID] ...
```

### 关键字管理

```bash
/add(/a) <关键字...>
/add_regex(/ar) <正则...>
/add_all(/aa) <关键字...>
/add_regex_all(/ara) <正则...>
/list_keyword(/lk)
/remove_keyword(/rk) <关键字...>
/remove_keyword_by_id(/rkbi) <ID...>
/remove_all_keyword(/rak) <关键字...>
/clear_all_keywords(/cak)
/clear_all_keywords_regex(/cakr)
/copy_keywords(/ck) <规则ID>
/copy_keywords_regex(/ckr) <规则ID>
```

### 替换规则

```bash
/replace(/r) <正则表达式> [替换内容]
/replace_all(/ra) <正则表达式> [替换内容]
/list_replace(/lrp)
/remove_replace(/rr) <序号>
/clear_all_replace(/car)
/copy_replace(/crp) <规则ID>
```

### 导入导出

```bash
/export_keyword(/ek)
/export_replace(/er)
/import_keyword(/ik) <同时发送文件>
/import_regex_keyword(/irk) <同时发送文件>
/import_replace(/ir) <同时发送文件>
```

### RSS / UFB

```bash
/delete_rss_user(/dru) [用户名]
/ufb_bind(/ub) <域名>
/ufb_unbind(/uu)
/ufb_item_change(/uic)
```

## 故障排查

### Bot 菜单里看不到新设置项

先确认两件事：

1. 容器是否已经运行最新代码
2. Telegram 客户端是否缓存了旧按钮界面

很多“代码已经更新但菜单没变”的情况，实际是 Telegram 客户端缓存，没有重新进入 `/settings` 页面。

### 消息没有转发

优先检查：

1. 源聊天是否真的是规则里的 `source_chat`
2. 规则是否启用
3. 当前消息是否在关键词、媒体、AI、RSS 或推送链路中被中断
4. 引用转发是否依赖历史转发映射
5. 容器日志和数据库规则状态是否一致

## 致谢

- [Telethon](https://github.com/LonamiWebs/Telethon)
- [Apprise](https://github.com/caronc/apprise)

## 开源协议

本项目基于 [GPL-3.0](LICENSE) 开源。
