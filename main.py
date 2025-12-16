import os
import json
import random
import asyncio
import time
import hashlib
import aiohttp
import difflib
from aiohttp import web

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.platform import AstrMessageEvent
# 【修正1】只引入最基础的组件，确保兼容所有版本
from astrbot.core.message.components import Image, Plain

@register("vv_meme_master", "MemeMaster", "GalleryStyle", "15.1.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        self.config_file = os.path.join(self.base_dir, "config.json")
        
        self.last_auto_save_time = 0
        
        if not os.path.exists(self.img_dir):
            os.makedirs(self.img_dir, exist_ok=True)
            
        self.local_config = self.load_config()
        self.data = self.load_data()

        # 启动网页后台
        try:
            asyncio.create_task(self.start_web_server())
        except Exception as e:
            print(f"Web后台启动异常: {e}")

    # ==============================================================
    # 逻辑部分 1：递小抄 & 自动鉴赏
    # ==============================================================
    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        img_url = self._get_img_url(event)
        
        # --- 分支 A：用户发图 (尝试自动进货) ---
        # 如果是图片，且不是在用存图命令，就跑去鉴赏
        if img_url and "/存图" not in event.message_str:
            cooldown = self.local_config.get("auto_save_cooldown", 60)
            if time.time() - self.last_auto_save_time > cooldown:
                asyncio.create_task(self.ai_evaluate_image(img_url))
            return

        # --- 分支 B：用户发文字 (准备发图) ---
        if not img_url:
            # 概率控制
            prob = self.local_config.get("reply_prob", 100)
            if random.randint(1, 100) > prob:
                return 

            descriptions = self.get_all_descriptions()
            if not descriptions:
                return
            
            # 随机抽 50 个给 AI 看，省 Token
            display_list = descriptions if len(descriptions) <= 50 else random.sample(descriptions, 50)
            menu_text = "、".join(display_list)
            
            # 注入小抄
            system_injection = f"\n\n[System Hint]\nAvailable Memes: [{menu_text}]\nUse 'MEME_TAG: content' to send."
            event.message_str += system_injection

    # ==============================================================
    # 逻辑部分 2：发图执行 (拦截 MEME_TAG)
    # ==============================================================
    @filter.on_decorating_result()
    async def on_decorate(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result:
            return
        text = result.message_str
        
        if "MEME_TAG:" in text:
            try:
                parts = text.split("MEME_TAG:")
                chat_content = parts[0].strip()
                # 提取 AI 选的标签描述
                selected_desc = parts[1].strip().split('\n')[0]
                
                img_path = self.find_best_match(selected_desc)
                
                if img_path:
                    print(f"🎯 AI发图: {selected_desc}")
                    # 【修正2】直接传列表，不要用 MessageChain
                    chain = [Plain(chat_content + "\n"), Image.fromFileSystem(img_path)]
                    event.set_result(chain)
                else:
                    event.set_result([Plain(chat_content)])
            except:
                pass

    # ==============================================================
    # 逻辑部分 3：AI 自动鉴赏 (自动存图)
    # ==============================================================
    async def ai_evaluate_image(self, img_url):
        try:
            self.last_auto_save_time = time.time()
            provider = self.context.get_using_provider()
            if not provider:
                return

            prompt = """
请判断这张图片是否适合作为"表情包"收藏。
标准：有趣、有梗、二次元或动物表情。普通照片不要。
如果不适合回: NO
如果适合，请提取特征，格式为：
YES
角色名：情绪/动作
"""
            resp = await provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            content = (getattr(resp, "completion_text", None) or getattr(resp, "text", "")).strip()

            if content.startswith("YES"):
                lines = content.splitlines()
                if len(lines) >= 2:
                    tag = lines[1].strip()
                    print(f"🖤 [自动进货] {tag}")
                    await self._save_image_file(img_url, tag, "auto")
        except Exception as e:
            print(f"鉴赏失败: {e}")

    # ==============================================================
    # Web 后台部分
    # ==============================================================
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
        print(f"WebUI started on port {port}")

    async def handle_index(self, r):
        p = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(p):
            return web.Response(text="index missing", status=404)
        with open(p, "r", encoding="utf-8") as f:
            # 渲染模板，兼容 HTML 中的 {{MEME_DATA}}
            return web.Response(text=f.read().replace("{{MEME_DATA}}", json.dumps(self.data)), content_type="text/html")

    # 【修正3】坚如磐石的上传逻辑
    async def handle_upload(self, r):
        try:
            reader = await r.multipart()
            
            # 临时变量
            file_data = None
            filename = None
            tags_text = "未分类"

            # 循环读取所有部分
            while True:
                part = await reader.next()
                if part is None:
                    break
                
                if part.name == "file":
                    filename = part.filename
                    file_data = await part.read()
                elif part.name == "tags":
                    # 确保读到文字
                    val = await part.text()
                    if val and val.strip():
                        tags_text = val.strip()

            # 全部读完再保存，确保标签不会丢失
            if file_data and filename:
                if os.path.exists(os.path.join(self.img_dir, filename)):
                    filename = f"{int(time.time())}_{filename}"
                
                with open(os.path.join(self.img_dir, filename), "wb") as f:
                    f.write(file_data)
                
                self.data[filename] = {"tags": tags_text, "source": "manual"}
                self.save_data()
                return web.Response(text="ok")
            
            return web.Response(text="missing file", status=400)
        except:
            return web.Response(text="error")

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
        if d.get("filename") in self.data:
            self.data[d.get("filename")]["tags"] = d.get("tags")
            self.save_data()
        return web.Response(text="ok")

    async def handle_get_config(self, r):
        return web.json_response(self.local_config)

    async def handle_update_config(self, r): 
        self.local_config.update(await r.json())
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.local_config, f, indent=2)
        except:
            pass
        return web.Response(text="ok")

    # ================== 工具函数 ==================
    def get_all_descriptions(self):
        if not self.data:
            return []
        return [info.get("tags", "") for info in self.data.values()]

    def find_best_match(self, query):
        best_file = None
        best_ratio = 0.0
        for filename, info in self.data.items():
            tags = info.get("tags", "")
            # 使用模糊匹配算法
            ratio = difflib.SequenceMatcher(None, query, tags).ratio()
            if query in tags:
                ratio += 0.5
            if ratio > best_ratio:
                best_ratio = ratio
                best_file = filename
        
        # 只要有一点相似度就发
        if best_ratio > 0.1 and best_file:
            return os.path.join(self.img_dir, best_file)
        return None

    @filter.command("存图")
    async def save_cmd(self, event: AstrMessageEvent):
        tags = event.message_str.replace("存图", "").strip()
        img_url = self._get_img_url(event)
        if not img_url:
            return await event.send("请回复图片")
        if not tags:
            return await event.send("❌ 请输入描述")
        await self._save_image_file(img_url, tags, "manual")
        await event.send(f"✅ 手动入库: {tags}")

    async def _save_image_file(self, url, tags, source):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        fn = f"{int(time.time())}.jpg"
                        content = await resp.read()
                        with open(os.path.join(self.img_dir, fn), "wb") as f:
                            f.write(content)
                        self.data[fn] = {"tags": tags, "source": source}
                        self.save_data()
        except:
            pass

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

    def load_config(self):
        default = {"web_port": 5000, "reply_prob": 100, "auto_save_cooldown": 60}
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f:
                    default.update(json.load(f))
        except:
            pass
        return default

    def load_data(self):
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r") as f:
                    return json.load(f)
        except:
            pass
        return {}

    def save_data(self):
        try:
            with open(self.data_file, "w") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except:
            pass
