print("🔥 vv_meme_master main.py imported")
import os
import json
import asyncio
import time
import hashlib
import random
import aiohttp
from aiohttp import web
import traceback

# 这里的引用保持原样
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
            try:
                os.makedirs(self.img_dir)
            except:
                pass

        self.data = self.load_data()
        self.local_config = self.load_config()

        # 启动网页服务（加了防崩溃保护）
        try:
            asyncio.create_task(self.start_web_server())
        except Exception as e:
            print(f"Web服务启动失败(不影响聊天): {e}")

    # ================== 发图 ==================

    @filter.command("来张图")
    async def send_meme_cmd(self, event: AstrMessageEvent):
        try:
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
        except Exception as e:
            print(f"发图功能出错: {e}")

    # ================== 手动存图 ==================

    @filter.command("存图")
    async def save_meme_cmd(self, event: AstrMessageEvent):
        try:
            tags = event.message_str.replace("存图", "").strip() or "未分类"

            img_url = self._get_img_url(event)
            if not img_url:
                await event.send("请附带图片或回复图片")
                return

            await self._download_and_save(img_url, tags, "manual")
            await event.send(f"✅ 已收录: {tags}")
        except Exception as e:
            await event.send(f"存图失败: {e}")

    # ================== 自动监听 (这里修好了) ==================

    # 还是用 ALL，因为您的版本没有 IMAGE，但是我们在里面加了保护
    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        # 【重点】超级保护罩：不管这里面发生什么错，绝不让 AI 变哑巴
        try:
            # 1. 尝试获取图片链接
            img_url = self._get_img_url(event)
            
            # 2. 如果这消息里没图片，那就不关我事，直接结束，让 AI 去处理
            if not img_url:
                return

            # 3. 检查冷却时间
            cooldown = self.local_config.get("pick_cooldown", 30)
            if time.time() - self.last_pick_time < cooldown:
                return

            # 4. 只有确实是图片，且冷却好了，才去后台偷偷运行 AI 识图
            # 使用 create_task 把它扔到后台去，不要卡住当前的对话
            asyncio.create_task(
                self.ai_evaluate_image(img_url, event.message_str)
            )
            
        except Exception:
            # 万一出了任何错，哪怕是天塌下来了，也只是在后台打印一下
            # 绝对不干扰正常聊天
            # print("插件后台小报错，不影响使用") # 为了清净这句也可以不打
            pass

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
            # 这里也是，出错了就打印一下，别影响主程序
            print(f"❌ 识图失败: {e}")

    # ================== 工具函数 ==================

    def _get_img_url(self, event):
        try:
            msg_obj = event.message_obj
            if hasattr(msg_obj, "message"):
                for comp in msg_obj.message:
                    if isinstance(comp, Image):
                        return comp.url
            if hasattr(msg_obj, "message_chain"):
                for comp in msg_obj.message_chain:
                    if isinstance(comp, Image):
                        return comp.url
        except:
            return None
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
        except Exception as e:
            print(f"存图写入失败: {e}")

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
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.local_config, f, indent=2)
        except:
            pass

    def load_data(self):
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def save_data(self):
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except:
            pass

    async def start_web_server(self):
        try:
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
            print(f"MemeMaster WebUI started on port {port}")
        except Exception as e:
            print(f"MemeMaster WebUI 启动失败 (端口可能被占用): {e}")

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
        try:
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
        except:
            pass
        return web.Response(text="fail", status=400)

    async def handle_delete(self, r):
        try:
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
        except:
            pass
        return web.Response(text="fail", status=404)

    async def handle_batch_delete(self, r):
        try:
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
        except:
            return web.Response(text="error", status=500)

    async def handle_update_tag(self, r):
        try:
            d = await r.json()
            fn = d.get("filename")
            t = d.get("tags")
            if fn in self.data:
                self.data[fn]["tags"] = t
                self.save_data()
                return web.Response(text="ok")
        except:
            pass
        return web.Response(text="fail", status=404)

    async def handle_get_config(self, r):
        return web.json_response(self.local_config)

    async def handle_update_config(self, r):
        try:
            self.local_config.update(await r.json())
            self.save_config()
            return web.Response(text="ok")
        except:
            return web.Response(text="error", status=500)
