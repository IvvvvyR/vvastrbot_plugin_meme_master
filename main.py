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
from astrbot.api.message_components import Image, Plain

@register("meme_master", "表情包大脑", "图床+捡垃圾+备份", "7.0.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        
        # 状态记录
        self.last_pick_time = 0 
        self.sent_count_hour = 0
        self.last_sent_reset = time.time()
        
        if not os.path.exists(self.img_dir):
            os.makedirs(self.img_dir)
            
        self.data = self.load_data()
        asyncio.create_task(self.start_web_server())

    # 面板配置项
    def config_schema():
        return [
            {"name": "web_port", "type": "int", "default": 5000, "description": "Web后台端口"},
            {"name": "pick_cooldown", "type": "int", "default": 30, "description": "捡垃圾冷却(秒)"},
            {"name": "reply_prob", "type": "int", "default": 80, "description": "发图概率(0-100)"},
            {"name": "max_per_hour", "type": "int", "default": 20, "description": "每小时发图上限"}
        ]

    def load_data(self):
        if not os.path.exists(self.data_file): return {}
        with open(self.data_file, "r", encoding="utf-8") as f:
            return json.load(f)

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

    # ================= Web 服务器 =================
    async def start_web_server(self):
        port = self.config.get("web_port", 5000)
        app = web.Application()
        app.router.add_get('/', self.handle_index)
        app.router.add_post('/upload', self.handle_upload)
        app.router.add_post('/delete', self.handle_delete)
        app.router.add_get('/backup', self.handle_backup) # 新增备份接口
        app.router.add_static('/images/', path=self.img_dir, name='images')
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            self.context.logger.info(f"✅ [表情包后台] 启动成功: 端口 {port}")
        except:
            self.context.logger.error(f"❌ 端口 {port} 启动失败，请检查占用")

    async def handle_index(self, request):
        html_path = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(html_path): return web.Response(text="缺少 index.html", status=404)
        with open(html_path, "r", encoding="utf-8") as f: html = f.read()
        html = html.replace("{{MEME_DATA}}", json.dumps(self.data))
        return web.Response(text=html, content_type='text/html')

    # 核心功能：一键备份下载
    async def handle_backup(self, request):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # 打包 json
            if os.path.exists(self.data_file):
                zip_file.write(self.data_file, "memes.json")
            # 打包所有图片
            for root, dirs, files in os.walk(self.img_dir):
                for file in files:
                    zip_file.write(os.path.join(root, file), os.path.join("images", file))
        
        buffer.seek(0)
        return web.Response(
            body=buffer,
            headers={
                'Content-Disposition': f'attachment; filename="meme_backup_{int(time.time())}.zip"',
                'Content-Type': 'application/zip'
            }
        )

    async def handle_upload(self, request):
        reader = await request.multipart()
        field = await reader.next()
        if field.name == 'file':
            filename = field.filename
            file_content = bytearray()
            while True:
                chunk = await field.read_chunk()
                if not chunk: break
                file_content.extend(chunk)
            
            img_hash = self.calculate_md5(file_content)
            if os.path.exists(os.path.join(self.img_dir, filename)):
                filename = f"{int(time.time())}_{filename}"
            with open(os.path.join(self.img_dir, filename), 'wb') as f:
                f.write(file_content)
            
            tags_field = await reader.next()
            tags = (await tags_field.text()).strip()
            self.data[filename] = {"tags": tags, "source": "manual", "hash": img_hash}
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=400)

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

    # ================= AI 发图 =================
    @llm_tool(name="express_emotion_with_image")
    async def express_emotion_with_image(self, emotion: str):
        """想用图片表达情绪或玩梗时调用。主动使用，不要等用户要求。Args: emotion (str): 情绪或意图"""
        # 限额检查
        if time.time() - self.last_sent_reset > 3600:
            self.sent_count_hour = 0
            self.last_sent_reset = time.time()
        
        limit = self.config.get("max_per_hour", 20)
        if self.sent_count_hour >= limit:
            return f"系统提示：每小时发图上限已达({limit}张)，暂停发图。"

        # 概率检查
        prob = self.config.get("reply_prob", 80)
        if random.randint(1, 100) > prob:
            return "系统提示：判定不用发图。"

        results = []
        for filename, info in self.data.items():
            tags = info.get("tags", "") if isinstance(info, dict) else info
            if emotion in tags or any(k in emotion for k in tags.split()):
                results.append(filename)
        
        if not results: return f"系统提示：无 '{emotion}' 相关图片。"

        selected_file = random.choice(results)
        file_path = os.path.join(self.img_dir, selected_file)
        await self.context.send_message(self.context.get_event_queue().get_nowait(), [Image.fromFileSystem(file_path)])
        self.sent_count_hour += 1
        return f"系统提示：已发图 [{selected_file}]"

    # ================= 捡垃圾 =================
    @event_message_type(MessageType.GROUP_MESSAGE, MessageType.FRIEND_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        msg = event.message_str
        trigger_words = ["记住", "存图", "收录"]
        found_trigger = next((w for w in trigger_words if w in msg), None)
        
        img_url = None
        for comp in event.message_obj.message_chain:
            if isinstance(comp, Image):
                img_url = comp.url
                break
        if not img_url: return

        # 场景1: 显式命令
        if found_trigger:
            tags = msg.replace(found_trigger, "").strip() or "未分类"
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        await self.save_image_bytes(content, tags, "manual", event)
            return

        # 场景2: 自动捡垃圾
        cooldown = self.config.get("pick_cooldown", 30)
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
            prompt = f"""请审视这张图。用户配文:"{context_text}"。结合图片和文字，判断是否值得收藏到表情包库。
            1. 无意义 -> NO
            2. 有趣/符合人设 -> YES|标签(10字内)"""
            
            handler = self.context.get_llm_handler()
            if not handler: return
            resp = await handler.provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            completion = resp.completion_text.strip()
            
            if completion.startswith("YES"):
                tags = completion.split("|")[-1].strip()
                self.context.logger.info(f"👀 [AI捡垃圾] 存入: {tags}")
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
                self.context.logger.info(f"✅ 手动收录: {tags}")
        except: pass
