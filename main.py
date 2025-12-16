import os
import json
import asyncio
import time
import hashlib
import random
import zipfile
import io
import aiohttp
from aiohttp import web
from astrbot.api.all import *

@register("vv_meme_master", "MemeMaster", "Web管理+强制发图修复版", "13.0.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        self.config_file = os.path.join(self.base_dir, "config.json")
        
        self.current_event = None 
        self.last_pick_time = 0 
        self.sent_count_hour = 0
        self.last_sent_reset = time.time()
        
        if not os.path.exists(self.img_dir): os.makedirs(self.img_dir)
        
        self.data = self.load_data()
        self.local_config = self.load_config()
        
        print(f"🔍 [MemeMaster] v13.0 加载完毕 | 图片库存: {len(self.data)}")
        asyncio.create_task(self.start_web_server())

    # --- 配置与数据加载 ---
    def load_config(self):
        default_conf = {"web_port": 5000, "pick_cooldown": 30, "reply_prob": 100, "max_per_hour": 999}
        if not os.path.exists(self.config_file): return default_conf
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default_conf.update(saved)
                return default_conf
        except: return default_conf

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.local_config, f, indent=2)

    def load_data(self):
        if not os.path.exists(self.data_file): return {}
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                clean_data = {}
                for k, v in raw_data.items():
                    if not k.lower().endswith(('.jpg', '.png', '.gif', '.jpeg', '.webp')): continue
                    if isinstance(v, str): clean_data[k] = {"tags": v, "source": "manual", "hash": ""}
                    else: clean_data[k] = v
                return clean_data
        except: return {}

    def save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def calculate_md5(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def is_duplicate(self, img_hash: str) -> bool:
        if not img_hash: return False
        for info in self.data.values():
            if isinstance(info, dict) and info.get("hash") == img_hash: return True
        return False

    # --- Web 服务 ---
    async def start_web_server(self):
        port = self.local_config.get("web_port", 5000)
        app = web.Application()
        app.router.add_get('/', self.handle_index)
        app.router.add_post('/upload', self.handle_upload)
        app.router.add_post('/delete', self.handle_delete)
        app.router.add_post('/batch_delete', self.handle_batch_delete)
        app.router.add_post('/update_tag', self.handle_update_tag)
        app.router.add_get('/backup', self.handle_backup)
        app.router.add_get('/get_config', self.handle_get_config)
        app.router.add_post('/update_config', self.handle_update_config)
        app.router.add_static('/images/', path=self.img_dir, name='images')
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            print(f"✅ [MemeMaster] Web启动成功: {port}")
        except: pass

    # (Web Handle Functions 省略，保持原样，为了不让代码太长)
    async def handle_index(self, request):
        html_path = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(html_path): return web.Response(text="index.html missing", status=404)
        with open(html_path, "r", encoding="utf-8") as f: html = f.read()
        html = html.replace("{{MEME_DATA}}", json.dumps(self.data))
        return web.Response(text=html, content_type='text/html')
    async def handle_get_config(self, request): return web.json_response(self.local_config)
    async def handle_update_config(self, request):
        try:
            new_conf = await request.json()
            self.local_config.update(new_conf)
            self.save_config()
            return web.Response(text="ok")
        except: return web.Response(text="fail", status=500)
    async def handle_backup(self, request):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if os.path.exists(self.data_file): zip_file.write(self.data_file, "memes.json")
            if os.path.exists(self.config_file): zip_file.write(self.config_file, "config.json")
            for root, dirs, files in os.walk(self.img_dir):
                for file in files: zip_file.write(os.path.join(root, file), os.path.join("images", file))
        buffer.seek(0)
        return web.Response(body=buffer, headers={'Content-Disposition': f'attachment; filename="meme_backup_{int(time.time())}.zip"', 'Content-Type': 'application/zip'})
    async def handle_update_tag(self, request):
        try:
            data = await request.json()
            filename = data.get("filename")
            new_tags = data.get("tags")
            if filename in self.data:
                if isinstance(self.data[filename], str): self.data[filename] = {"tags": self.data[filename], "source": "manual", "hash": ""}
                self.data[filename]["tags"] = new_tags
                self.save_data()
                return web.Response(text="ok")
            return web.Response(text="fail", status=404)
        except: return web.Response(text="error", status=500)
    async def handle_batch_delete(self, request):
        try:
            data = await request.json()
            filenames = data.get("filenames", [])
            for filename in filenames:
                if filename in self.data:
                    try: os.remove(os.path.join(self.img_dir, filename))
                    except: pass
                    del self.data[filename]
            self.save_data()
            return web.Response(text="ok")
        except: return web.Response(text="fail", status=500)
    async def handle_upload(self, request):
        reader = await request.multipart()
        file_data = None; filename = None; tags = "未分类"
        while True:
            field = await reader.next()
            if field is None: break
            if field.name == 'file':
                filename = field.filename
                if not filename: continue 
                file_data = await field.read()
            elif field.name == 'tags': tags = (await field.text()).strip() or "未分类"
        if file_data and filename:
            if not filename.lower().endswith(('.jpg', '.png', '.gif', '.jpeg', '.webp')): return web.Response(text="invalid file type", status=400)
            img_hash = self.calculate_md5(file_data)
            if os.path.exists(os.path.join(self.img_dir, filename)): filename = f"{int(time.time())}_{filename}"
            with open(os.path.join(self.img_dir, filename), 'wb') as f: f.write(file_data)
            self.data[filename] = {"tags": tags, "source": "manual", "hash": img_hash}
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="no file", status=400)
    async def handle_delete(self, request):
        data = await request.json()
        filename = data.get("filename")
        if filename in self.data:
            try: os.remove(os.path.join(self.img_dir, filename))
            except: pass
            del self.data[filename]
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)


    # =========================================================
    # 核心修复 1：加上了 Docstring 说明书
    # =========================================================

    @llm_tool(name="express_emotion_with_image")
    async def express_emotion_with_image(self, emotion: str):
        """
        发送表情包工具。
        当用户表达强烈情绪（如开心、难过、生气、哭、疑问）或明确要求看表情包时，
        请务必调用此工具来发送图片，而不是仅用文字描述。
        
        :param emotion: 情绪关键词，例如：开心、难过、生气、搞怪、嘲讽、疑问
        """
        print(f"👉 [Debug] 成功触发发图工具，参数: {emotion}")
        
        if not self.current_event:
            print("❌ [Debug] 失败：没有找到发图对象 (current_event is None)")
            return "系统错误：找不到目标。"

        results = []
        for filename, info in self.data.items():
            tags = info.get("tags", "") if isinstance(info, dict) else info
            if emotion in tags or any(k in emotion for k in tags.split()):
                results.append(filename)
        
        if not results: 
            # 备选：如果没找到，就随机发一张，防止空消息
            print(f"⚠️ [Debug] 没找到 '{emotion}'，随机发一张兜底")
            if self.data: results = list(self.data.keys())
            else: return "系统提示：图库是空的。"
            
        selected_file = random.choice(results)
        file_path = os.path.join(self.img_dir, selected_file)
        
        try:
            print(f"🚀 [Debug] 正在发送: {selected_file}")
            await self.context.send_message(self.current_event, [Image.fromFileSystem(file_path)])
            return f"系统提示：已发送图片 [{selected_file}]"
        except Exception as e:
            print(f"❌ [Debug] 发送报错: {e}")
            return f"系统错误：{e}"

    
    # =========================================================
    # 核心修复 2：On_Message 里的强制触发逻辑
    # =========================================================

    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        self.current_event = event # 锁定当前说话的人
        
        msg_obj = event.message_obj
        msg = event.message_str
        
        # --- 强制测试开关 ---
        # 只要你说 "来张图 哭" 或者 "发表情 哭"，就绕过 AI 直接发！
        if msg.startswith("来张图") or msg.startswith("发表情"):
            keyword = msg.replace("来张图", "").replace("发表情", "").strip()
            if not keyword: keyword = "搞怪" # 默认词
            print(f"🔥 [Debug] 强制触发模式: {keyword}")
            await self.express_emotion_with_image(keyword)
            return # 强制发完就结束，不给 AI 处理了
        # ------------------

        # 后面是正常的收图逻辑
        img_url = None
        if hasattr(msg_obj, "message"):
            for comp in msg_obj.message:
                if isinstance(comp, Image): img_url = comp.url; break
        if not img_url and hasattr(msg_obj, "message_chain"):
             for comp in msg_obj.message_chain:
                if isinstance(comp, Image): img_url = comp.url; break

        if not img_url: return

        trigger_words = ["记住", "存图", "收录"]
        found_trigger = next((w for w in trigger_words if w in msg), None)
        
        if found_trigger:
            tags = msg.replace(found_trigger, "").strip() or "未分类"
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        await self.save_image_bytes(content, tags, "manual", event)
            return
        
        cooldown = self.local_config.get("pick_cooldown", 30)
        if time.time() - self.last_pick_time < cooldown: return
        asyncio.create_task(self.ai_evaluate_image(img_url, context_text=msg))

    async def ai_evaluate_image(self, img_url, context_text=""):
        try:
            content = None
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200: content = await resp.read()
            if not content: return
            img_hash = self.calculate_md5(content)
            if self.is_duplicate(img_hash): return 
            
            self.last_pick_time = time.time()
            prompt = f"""请审视这张图。配文:"{context_text}"。1.无意义->NO 2.有趣->YES|标签(10字内)"""
            handler = self.context.get_llm_handler()
            if not handler: return
            resp = await handler.provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            completion = resp.completion_text.strip()
            if completion.startswith("YES"):
                tags = completion.split("|")[-1].strip()
                print(f"🖤 [机在捡垃圾] 存入: {tags}")
                await self.save_image_bytes(content, tags, "auto", None, img_hash)
        except: pass

    async def save_image_bytes(self, content, tags, source, event=None, precalc_hash=None):
        try:
            file_name = f"{int(time.time())}.jpg"
            save_path = os.path.join(self.img_dir, file_name)
            img_hash = precalc_hash if precalc_hash else self.calculate_md5(content)
            with open(save_path, 'wb') as f: f.write(content)
            self.data[file_name] = {"tags": tags, "source": source, "hash": img_hash}
            self.save_data()
            if source == "manual" and event:
                print(f"✅ 手动收录: {tags}")
        except: pass
