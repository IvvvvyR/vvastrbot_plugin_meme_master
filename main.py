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

@register("vv_meme_master", "MemeMaster", "类型注解修复版", "13.2.0")
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
        
        if not os.path.exists(self.img_dir): 
            os.makedirs(self.img_dir)
        
        self.data = self.load_data()
        self.local_config = self.load_config()
        
        print(f"🔍 [MemeMaster] v13.2 加载成功 | 图片数: {len(self.data)}")
        asyncio.create_task(self.start_web_server())

    def load_config(self):
        default_conf = {
            "web_port": 5000, 
            "pick_cooldown": 30, 
            "reply_prob": 100, 
            "max_per_hour": 999
        }
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
                raw_data = json.load(f)
                clean_data = {}
                for k, v in raw_data.items():
                    if not k.lower().endswith(('.jpg', '.png', '.gif', '.jpeg', '.webp')): 
                        continue
                    if isinstance(v, str): 
                        clean_data[k] = {"tags": v, "source": "manual", "hash": ""}
                    else: 
                        clean_data[k] = v
                return clean_data
        except: 
            return {}

    def save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def calculate_md5(self, content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def is_duplicate(self, img_hash: str) -> bool:
        if not img_hash: 
            return False
        for info in self.data.values():
            if isinstance(info, dict) and info.get("hash") == img_hash: 
                return True
        return False

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
        except: 
            pass

    async def handle_index(self, request):
        html_path = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(html_path): 
            return web.Response(text="index.html missing", status=404)
        with open(html_path, "r", encoding="utf-8") as f: 
            html = f.read()
        html = html.replace("{{MEME_DATA}}", json.dumps(self.data))
        return web.Response(text=html, content_type='text/html')
    
    async def handle_get_config(self, request): 
        return web.json_response(self.local_config)
    
    async def handle_update_config(self, request):
        try:
            new_conf = await request.json()
            self.local_config.update(new_conf)
            self.save_config()
            return web.Response(text="ok")
        except: 
            return web.Response(text="fail", status=500)
    
    async def handle_backup(self, request):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if os.path.exists(self.data_file): 
                zip_file.write(self.data_file, "memes.json")
            if os.path.exists(self.config_file): 
                zip_file.write(self.config_file, "config.json")
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
    
    async def handle_update_tag(self, request):
        try:
            data = await request.json()
            filename = data.get("filename")
            new_tags = data.get("tags")
            if filename in self.data:
                if isinstance(self.data[filename], str): 
                    self.data[filename] = {
                        "tags": self.data[filename], 
                        "source": "manual", 
                        "hash": ""
                    }
                self.data[filename]["tags"] = new_tags
                self.save_data()
                return web.Response(text="ok")
            return web.Response(text="fail", status=404)
        except: 
            return web.Response(text="error", status=500)
    
    async def handle_batch_delete(self, request):
        try:
            data = await request.json()
            filenames = data.get("filenames", [])
            for filename in filenames:
                if filename in self.data:
                    try: 
                        os.remove(os.path.join(self.img_dir, filename))
                    except: 
                        pass
                    del self.data[filename]
            self.save_data()
            return web.Response(text="ok")
        except: 
            return web.Response(text="fail", status=500)
    
    async def handle_upload(self, request):
        reader = await request.multipart()
        file_data = None
        filename = None
        tags = "未分类"
        
        while True:
            field = await reader.next()
            if field is None: 
                break
            if field.name == 'file':
                filename = field.filename
                if not filename: 
                    continue 
                file_data = await field.read()
            elif field.name == 'tags': 
                tags = (await field.text()).strip() or "未分类"
        
        if file_data and filename:
            if not filename.lower().endswith(('.jpg', '.png', '.gif', '.jpeg', '.webp')): 
                return web.Response(text="invalid file type", status=400)
            img_hash = self.calculate_md5(file_data)
            if os.path.exists(os.path.join(self.img_dir, filename)): 
                filename = f"{int(time.time())}_{filename}"
            with open(os.path.join(self.img_dir, filename), 'wb') as f: 
                f.write(file_data)
            self.data[filename] = {"tags": tags, "source": "manual", "hash": img_hash}
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="no file", status=400)
    
    async def handle_delete(self, request):
        data = await request.json()
        filename = data.get("filename")
        if filename in self.data:
            try: 
                os.remove(os.path.join(self.img_dir, filename))
            except: 
                pass
            del self.data[filename]
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)

    # =================================================================
    # 🔥 核心工具函数 - 确保类型注解完整
    # =================================================================
    
    @llm_tool(name="send_meme_image")
    async def send_meme_image(self, keyword: str) -> str:
        """
        根据关键词发送匹配的表情包图片。
        
        **使用场景：**
        1. 用户明确要求发图："发张图"、"来个表情包"、"表情包"
        2. 用户表达强烈情绪且希望看图："我好开心（想看图）"
        3. 用户询问："有没有xx的图"
        
        **参数说明：**
        keyword: 表情包的标签关键词，如"搞怪"、"开心"、"难过"、"可爱"等
                 如果用户没有明确指定，根据上下文情绪推测，默认"搞怪"
        
        **调用示例：**
        - 用户："来张搞怪的图" → keyword="搞怪"
        - 用户："我好难过啊，发个图" → keyword="难过"
        - 用户："发个表情包" → keyword="搞怪"
        
        **返回值：**
        返回发送结果的文本描述，如"✅ 已发送表情包（标签：开心）"
        """
        print(f"👉 [工具调用] send_meme_image | 关键词: {keyword}")
        
        if not self.current_event:
            print("❌ [错误] current_event 为 None")
            return "系统错误：无法获取消息上下文"

        # 搜索匹配的图片
        matched_files = []
        keyword_lower = keyword.lower()
        
        for filename, info in self.data.items():
            tags = info.get("tags", "") if isinstance(info, dict) else info
            tags_lower = tags.lower()
            
            # 模糊匹配
            if keyword_lower in tags_lower or any(
                word in keyword_lower for word in tags_lower.split()
            ):
                matched_files.append(filename)
        
        # 如果没找到，随机发一张
        if not matched_files:
            print(f"⚠️ 没有找到标签'{keyword}'的图片，随机选择")
            if self.data:
                matched_files = list(self.data.keys())
            else:
                return "图库是空的呢，还没有收录任何表情包～"
        
        # 随机选择一张
        selected = random.choice(matched_files)
        file_path = os.path.join(self.img_dir, selected)
        
        # 发送图片
        try:
            print(f"📤 [发送] {selected}")
            await self.context.send_message(
                self.current_event, 
                [Image.fromFileSystem(file_path)]
            )
            tag_info = self.data[selected].get('tags', '未知') if isinstance(
                self.data[selected], dict
            ) else self.data[selected]
            return f"✅ 已发送表情包（标签：{tag_info}）"
        except Exception as e:
            print(f"❌ [发送失败] {e}")
            return f"发送失败：{str(e)}"

    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        self.current_event = event 
        msg = event.message_str
        
        # 强制触发后门（用于测试）
        if msg.startswith("来张图") or msg.startswith("发表情"):
            kw = msg.replace("来张图", "").replace("发表情", "").strip() or "搞怪"
            await self.send_meme_image(kw)
            return

        # 收图逻辑
        msg_obj = event.message_obj
        img_url = None
        if hasattr(msg_obj, "message"):
            for comp in msg_obj.message:
                if isinstance(comp, Image): 
                    img_url = comp.url
                    break
        if not img_url and hasattr(msg_obj, "message_chain"):
             for comp in msg_obj.message_chain:
                if isinstance(comp, Image): 
                    img_url = comp.url
                    break

        if not img_url: 
            return

        if "记住" in msg or "存图" in msg:
            tags = msg.replace("记住", "").replace("存图", "").strip() or "未分类"
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        await self.save_image_bytes(content, tags, "manual", event)
            return
        
        cooldown = self.local_config.get("pick_cooldown", 30)
        if time.time() - self.last_pick_time < cooldown: 
            return
        asyncio.create_task(self.ai_evaluate_image(img_url, context_text=msg))

    async def ai_evaluate_image(self, img_url: str, context_text: str = ""):
        try:
            content = None
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200: 
                        content = await resp.read()
            if not content: 
                return
            img_hash = self.calculate_md5(content)
            if self.is_duplicate(img_hash): 
                return 
            
            self.last_pick_time = time.time()
            prompt = f"""请审视这张图。配文:"{context_text}"。1.无意义->NO 2.有趣->YES|标签(10字内)"""
            handler = self.context.get_llm_handler()
            if not handler: 
                return
            resp = await handler.provider.text_chat(
                prompt, 
                session_id=None, 
                image_urls=[img_url]
            )
            completion = resp.completion_text.strip()
            if completion.startswith("YES"):
                tags = completion.split("|")[-1].strip()
                print(f"🖤 [捡垃圾] 存入: {tags}")
                await self.save_image_bytes(content, tags, "auto", None, img_hash)
        except: 
            pass

    async def save_image_bytes(
        self, 
        content: bytes, 
        tags: str, 
        source: str, 
        event: AstrMessageEvent = None, 
        precalc_hash: str = None
    ):
        try:
            file_name = f"{int(time.time())}.jpg"
            save_path = os.path.join(self.img_dir, file_name)
            img_hash = precalc_hash if precalc_hash else self.calculate_md5(content)
            with open(save_path, 'wb') as f: 
                f.write(content)
            self.data[file_name] = {"tags": tags, "source": source, "hash": img_hash}
            self.save_data()
            if source == "manual" and event:
                print(f"✅ 手动收录: {tags}")
        except: 
            pass
