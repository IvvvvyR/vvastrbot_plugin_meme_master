print("🔥 vv_meme_master main.py imported")
import os
import json
import asyncio
import time
import hashlib
import random
import aiohttp
from aiohttp import web

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.message.components import Image


@register("vv_meme_master", "MemeMaster", "GalleryStyle", "15.1.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config is not None else {}

        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        self.config_file = os.path.join(self.base_dir, "config.json")

        self.last_pick_time = 0

        if not os.path.exists(self.img_dir):
            os.makedirs(self.img_dir)

        self.data = self.load_data()
        self.local_config = self.load_config()

        asyncio.create_task(self.start_web_server())

    # ================== 发图 ==================

    @filter.command("来张图")
    async def send_meme_cmd(self, event: AstrMessageEvent):
        msg = event.message_str.replace("来张图", "").strip()
        kw = msg or ""

        results = []
        for fn, info in self.data.items():
            tags = info.get("tags", "")
            if kw in tags:
                results.append(fn)

        if not results and not kw:
            results = list(self.data.keys())

        if results:
            sel = random.choice(results)
            await event.send(Image.fromFileSystem(os.path.join(self.img_dir, sel)))
        else:
            await event.send("没找到这种图哦")

    # ================== 手动存图 ==================

    @filter.command("存图")
    async def save_meme_cmd(self, event: AstrMessageEvent):
        tags = event.message_str.replace("存图", "").strip() or "未分类"

        img_url = self._get_img_url(event)
        if not img_url:
            await event.send("请附带图片或回复图片")
            return

        await self._download_and_save(img_url, tags, "manual")
        await event.send(f"✅ 已收录: {tags}")

    # ================== 自动监听 ==================

    @filter.event_message_type(EventMessageType.IMAGE)
    async def on_message(self, event: AstrMessageEvent):
        img_url = self._get_img_url(event)
        if not img_url:
            return

        cooldown = self.local_config.get("pick_cooldown", 30)
        if time.time() - self.last_pick_time < cooldown:
            return

        asyncio.create_task(
            self.ai_evaluate_image(img_url, event.message_str)
        )

    # ================== 核心：AI 判断是否存图 ==================

    async def ai_evaluate_image(self, img_url, context_text=""):
        try:
            self.last_pick_time = time.time()

            provider = self.context.get_using_provider()
            if not provider:
                return

            prompt = f"""
你正在帮我整理一个 QQ 表情包素材库。

配文是：“{context_text}”。

请判断这张图片是否“值得被保存”为聊天表情包素材。

使用环境说明：
- 偏二次元 / meme
- 常见来源包括：chiikawa、这狗、线条小狗、多栋、猫meme
- 不要把普通照片当成表情包

如果不适合保存，只回复：
NO

如果适合保存，请严格按下面格式回复（不要多余内容）：

YES
<名称>:<一句说明这个表情包在什么语境下使用>

规则：
1. 如果你能明确判断这是某个常见 IP / 系列，请使用大家认得的名字
2. 如果无法判断 IP，不要硬编，用简短情绪或语气作为名称
3. 冒号后必须是一句自然语言说明
"""

            resp = await provider.text_chat(
                prompt,
                session_id=None,
                image_urls=[img_url]
            )

            content = (
                getattr(resp, "completion_text", None)
                or getattr(resp, "text", "")
            ).strip()

            if not content.startswith("YES"):
                return

            lines = content.splitlines()
            if len(lines) >= 2 and ":" in lines[1]:
                tag = lines[1].strip()
            else:
                tag = "未分类:未能清晰识别表情语义"

            print(f"🖤 [AI存图] {tag}")
            await self._download_and_save(img_url, tag, "auto")

        except Exception as e:
            print(f"❌ 识图失败: {e}")

    # ================== 工具函数 ==================

    def _get_img_url(self, event):
        msg_obj = event.message_obj
        if hasattr(msg_obj, "message"):
            for comp in msg_obj.message:
                if isinstance(comp, Image):
                    return comp.url
        if hasattr(msg_obj, "message_chain"):
            for comp in msg_obj.message_chain:
                if isinstance(comp, Image):
                    return comp.url
        return None

    async def _download_and_save(self, url, tags, source):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return

                    content = await resp.read()
                    md5 = hashlib.md5(content).hexdigest()

                    for v in self.data.values():
                        if v.get("hash") == md5:
                            return

                    fn = f"{int(time.time())}.jpg"
                    with open(os.path.join(self.img_dir, fn), "wb") as f:
                        f.write(content)

                    self.data[fn] = {
                        "tags": tags,
                        "source": source,
                        "hash": md5
                    }
                    self.save_data()
        except:
            pass

    # ================== Web / 配置 ==================

    def load_config(self):
        default_conf = {"web_port": 5000, "pick_cooldown": 30, "reply_prob": 100}
        if not os.path.exists(self.config_file):
            return default_conf
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_conf.update(saved)
                return default_conf
        except:
            return default_conf

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.local_config, f, indent=2)

    def load_data(self):
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    async def start_web_server(self):
        port = self.local_config.get("web_port", 5000)
        app = web.Application()
        app.router.add_get("/", self.handle_index)
        app.router.add_post("/upload", self.handle_upload)
        app.router.add_post("/delete", self.handle_delete)
        app.router.add_post("/batch_delete", self.handle_batch_delete)
        app.router.add_post("/update_tag", self.handle_update_tag)
        app.router.add_get("/get_config", self.handle_get_config)
        app.router.add_post("/update_config", self.handle_update_config)
        app.router.add_static("/images/", path=self.img_dir, name="images")

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()

    async def handle_index(self, r):
        p = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(p):
            return web.Response(text="index.html missing", status=404)
        with open(p, "r", encoding="utf-8") as f:
            h = f.read()
        return web.Response(
            text=h.replace("{{MEME_DATA}}", json.dumps(self.data)),
            content_type="text/html"
        )

    async def handle_upload(self, r):
        reader = await r.multipart()
        fd = None
        fn = None
        tags = "未分类"

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                fn = part.filename
                fd = await part.read()
            elif part.name == "tags":
                tags = (await part.text()).strip() or "未分类"

        if fd and fn:
            md5 = hashlib.md5(fd).hexdigest()
            if os.path.exists(os.path.join(self.img_dir, fn)):
                fn = f"{int(time.time())}_{fn}"
            with open(os.path.join(self.img_dir, fn), "wb") as f:
                f.write(fd)
            self.data[fn] = {"tags": tags, "source": "manual", "hash": md5}
            self.save_data()
            return web.Response(text="ok")

        return web.Response(text="fail", status=400)

    async def handle_delete(self, r):
        d = await r.json()
        fn = d.get("filename")
        if fn in self.data:
            try:
                os.remove(os.path.join(self.img_dir, fn))
            except:
                pass
            del self.data[fn]
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)

    async def handle_batch_delete(self, r):
        d = await r.json()
        for fn in d.get("filenames", []):
            if fn in self.data:
                try:
                    os.remove(os.path.join(self.img_dir, fn))
                except:
                    pass
                del self.data[fn]
        self.save_data()
        return web.Response(text="ok")

    async def handle_update_tag(self, r):
        d = await r.json()
        fn = d.get("filename")
        t = d.get("tags")
        if fn in self.data:
            self.data[fn]["tags"] = t
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)

    async def handle_get_config(self, r):
        return web.json_response(self.local_config)

    async def handle_update_config(self, r):
        self.local_config.update(await r.json())
        self.save_config()
        return web.Response(text="ok")
