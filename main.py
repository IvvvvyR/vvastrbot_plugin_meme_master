import os
import json
import asyncio
import time
import hashlib
import random
import aiohttp
from aiohttp import web

# 照抄 gallery 的导入方式，保证兼容性
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.message.components import Image

@register("vv_meme_master", "MemeMaster", "GalleryStyle", "15.0.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        
        # 路径初始化
        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        self.config_file = os.path.join(self.base_dir, "config.json")
        
        # 运行时状态
        self.last_pick_time = 0
        
        if not os.path.exists(self.img_dir): os.makedirs(self.img_dir)
        self.data = self.load_data()
        self.local_config = self.load_config()
        
        # 启动 WebUI
        asyncio.create_task(self.start_web_server())

    # ================== 基础指令 (抄作业部分) ==================

    # 1. 发表情指令
    @filter.command("来张图")
    async def send_meme_cmd(self, event: AstrMessageEvent):
        '''随机发送一张表情包，可接关键词'''
        msg = event.message_str.replace("来张图", "").strip()
        kw = msg or "" # 默认随机
        
        # 简单的匹配逻辑
        results = []
        for fn, info in self.data.items():
            tags = info.get("tags", "") if isinstance(info, dict) else info
            if kw in tags: results.append(fn)
        
        if not results and not kw: # 如果没关键词且没找到，就全库随机
            results = list(self.data.keys())
            
        if results:
            sel = random.choice(results)
            await event.send(Image.fromFileSystem(os.path.join(self.img_dir, sel)))
        else:
            await event.send("没找到这种图哦")

    # 2. 手动存图指令
    @filter.command("存图")
    async def save_meme_cmd(self, event: AstrMessageEvent):
        '''保存图片，格式：存图 关键词'''
        tags = event.message_str.replace("存图", "").strip() or "未分类"
        
        # 获取图片 (逻辑复用)
        img_url = self._get_img_url(event)
        if not img_url:
            await event.send("请附带图片或回复图片")
            return

        await self._download_and_save(img_url, tags, "manual")
        await event.send(f"✅ 已收录: {tags}")

    # ================== 自动监听 (抄作业部分) ==================

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        # 自动识图收录逻辑
        img_url = self._get_img_url(event)
        if not img_url: return

        # 冷却时间检查
        cooldown = self.local_config.get("pick_cooldown", 30)
        if time.time() - self.last_pick_time < cooldown: return

        # 异步去问 AI，不阻塞主线程
        asyncio.create_task(self.ai_evaluate_image(img_url, event.message_str))

    # ================== 核心功能函数 ==================

    def _get_img_url(self, event):
        '''提取图片URL的通用方法'''
        msg_obj = event.message_obj
        if hasattr(msg_obj, "message"):
            for comp in msg_obj.message:
                if isinstance(comp, Image): return comp.url
        if hasattr(msg_obj, "message_chain"):
             for comp in msg_obj.message_chain:
                if isinstance(comp, Image): return comp.url
        return None

    async def _download_and_save(self, url, tags, source):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        md5 = hashlib.md5(content).hexdigest()
                        
                        # 查重
                        for v in self.data.values():
                            if v.get("hash") == md5: return
                        
                        fn = f"{int(time.time())}.jpg"
                        with open(os.path.join(self.img_dir, fn), 'wb') as f: f.write(content)
                        self.data[fn] = {"tags": tags, "source": source, "hash": md5}
                        self.save_data()
        except: pass

    async def ai_evaluate_image(self, img_url, context_text=""):
        try:
            self.last_pick_time = time.time()
            provider = self.context.get_using_provider()
            if not provider: return

            # 直接问 AI，不通过 Tool 系统
            prompt = f"请看这张图。配文是：“{context_text}”。如果这张图适合做表情包，请回复：YES|标签(空格分隔)。否则回复NO。"
            response = await provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            completion = response.completion_text.strip()
            
            if completion.startswith("YES"):
                tags = completion.split("|")[-1].strip()
                print(f"🖤 [AI捡垃圾] 收录: {tags}")
                await self._download_and_save(img_url, tags, "auto")
        except Exception as e:
            print(f"❌ 识图失败: {e}")

    # ================== 辅助函数 (配置/Web) ==================
    # (这部分代码没有变，保持原样即可，为了完整性我贴在这里)
    
    def load_config(self):
        default_conf = {"web_port": 5000, "pick_cooldown": 30, "reply_prob": 100}
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
            with open(self.data_file, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}

    def save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    async def start_web_server(self):
        port = self.local_config.get("web_port", 5000)
        app = web.Application()
        app.router.add_get('/', self.handle_index)
        app.router.add_post('/upload', self.handle_upload)
        app.router.add_post('/delete', self.handle_delete)
        app.router.add_post('/batch_delete', self.handle_batch_delete)
        app.router.add_post('/update_tag', self.handle_update_tag)
        app.router.add_get('/get_config', self.handle_get_config)
        app.router.add_post('/update_config', self.handle_update_config)
        app.router.add_static('/images/', path=self.img_dir, name='images')
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
        except: pass

    async def handle_index(self, r):
        p = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(p): return web.Response(text="index.html missing", status=404)
        with open(p, "r", encoding="utf-8") as f: h = f.read()
        return web.Response(text=h.replace("{{MEME_DATA}}", json.dumps(self.data)), content_type='text/html')
    
    async def handle_upload(self, r):
        reader = await r.multipart()
        fd = None; fn = None; tags = "未分类"
        while True:
            f = await reader.next()
            if f is None: break
            if f.name == 'file':
                fn = f.filename; 
                if fn: fd = await f.read()
            elif f.name == 'tags': tags = (await f.text()).strip() or "未分类"
        if fd and fn:
            md5 = hashlib.md5(fd).hexdigest()
            if os.path.exists(os.path.join(self.img_dir, fn)): fn = f"{int(time.time())}_{fn}"
            with open(os.path.join(self.img_dir, fn), 'wb') as f: f.write(fd)
            self.data[fn] = {"tags": tags, "source": "manual", "hash": md5}
            self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=400)
    
    async def handle_delete(self, r):
        d = await r.json(); fn = d.get("filename")
        if fn in self.data:
            try: os.remove(os.path.join(self.img_dir, fn))
            except: pass
            del self.data[fn]; self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)
    async def handle_batch_delete(self, r):
        d = await r.json()
        for fn in d.get("filenames", []):
            if fn in self.data:
                try: os.remove(os.path.join(self.img_dir, fn))
                except: pass
                del self.data[fn]
        self.save_data()
        return web.Response(text="ok")
    async def handle_update_tag(self, r):
        d = await r.json(); fn = d.get("filename"); t = d.get("tags")
        if fn in self.data:
            self.data[fn]["tags"] = t; self.save_data()
            return web.Response(text="ok")
        return web.Response(text="fail", status=404)
    async def handle_get_config(self, r): return web.json_response(self.local_config)
    async def handle_update_config(self, r):
        self.local_config.update(await r.json()); self.save_config()
        return web.Response(text="ok")
