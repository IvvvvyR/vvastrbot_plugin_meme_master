import os
import json
import asyncio
import time
import hashlib
import random
import re
import aiohttp
from aiohttp import web

# 基础导入
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.message.components import Image

@register("vv_meme_master", "MemeMaster", "FinalFull", "14.1.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.base_dir = os.path.dirname(__file__)
        self.img_dir = os.path.join(self.base_dir, "images")
        self.data_file = os.path.join(self.base_dir, "memes.json")
        self.config_file = os.path.join(self.base_dir, "config.json")
        
        self.last_pick_time = 0 
        
        if not os.path.exists(self.img_dir): os.makedirs(self.img_dir)
        
        self.data = self.load_data()
        self.local_config = self.load_config()
        
        print(f"🔍 [MemeMaster] v14.1 (完全体) 就绪 | 库存: {len(self.data)}")
        asyncio.create_task(self.start_web_server())

    # --- 基础配置 ---
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
            with open(self.data_file, "r", encoding="utf-8") as f:
                return json.load(f)
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

    # --- WebUI (保持不变) ---
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
        if not os.path.exists(p): return web.Response(text="No index.html", status=404)
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
            md5 = self.calculate_md5(fd)
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

    # ================= 核心能力 1：AI 发表情 (暗号法) =================
    
    @filter.on_decorating_prompt()
    async def on_decorating_prompt(self, event: AstrMessageEvent):
        prompt = """
        【表情包调用协议】
        当你想用表情包表达情绪（如开心、嘲讽、疑问、哭），或用户要求发图时：
        请在回复中输出暗号：(MEME: 关键词)
        例如：(MEME: 哭)
        这非常重要！
        """
        event.add_system_prompt(prompt)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        text = resp.completion_text
        match = re.search(r"\(MEME:\s*(.*?)\)", text)
        if match:
            kw = match.group(1).strip()
            print(f"👉 [Meme] 触发暗号: {kw}")
            
            # 找图
            results = []
            for fn, info in self.data.items():
                tags = info.get("tags", "") if isinstance(info, dict) else info
                if kw in tags or any(k in kw for k in tags.split()):
                    results.append(fn)
            
            # 移除暗号
            resp.completion_text = text.replace(match.group(0), "").strip()
            
            if results:
                sel = random.choice(results)
                p = os.path.join(self.img_dir, sel)
                # 异步发图
                await event.send(Image.fromFileSystem(p))
            else:
                print(f"⚠️ [Meme] 没找到图: {kw}")

    # ================= 核心能力 2：AI 自动收图 (回归！) =================

    async def ai_evaluate_image(self, img_url, context_text=""):
        """
        手动调用 LLM 进行识图，不依赖 llm_tool 注册机制
        """
        try:
            # 下载图片
            content = None
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as resp:
                    if resp.status == 200: content = await resp.read()
            if not content: return

            # 查重
            img_hash = self.calculate_md5(content)
            if self.is_duplicate(img_hash): return 
            
            self.last_pick_time = time.time()
            
            # === 这里是重点：直接调用 Provider ===
            provider = self.context.get_using_provider() # 获取当前正在用的 AI 模型
            if not provider: return

            prompt = f"请看这张图。配文是：“{context_text}”。如果这张图适合做表情包，请回复：YES|标签(用空格分隔)。如果不适合或无意义，回复：NO。"
            
            # 调用 AI (这跟插件系统无关，是直接调接口，所以不会报错)
            response = await provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            
            completion = response.completion_text.strip()
            if completion.startswith("YES"):
                # 提取标签
                tags = completion.split("|")[-1].strip()
                print(f"🖤 [AI捡垃圾] 成功收录: {tags}")
                
                # 保存
                fn = f"{int(time.time())}.jpg"
                with open(os.path.join(self.img_dir, fn), 'wb') as f: f.write(content)
                self.data[fn] = {"tags": tags, "source": "auto", "hash": img_hash}
                self.save_data()
        except Exception as e:
            print(f"❌ [AI识图错误] {e}")

    # ================= 消息监听 =================

    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        msg = event.message_str
        msg_obj = event.message_obj
        
        # 强制指令
        if msg.startswith("来张图") or msg.startswith("发表情"):
            kw = msg.replace("来张图", "").replace("发表情", "").strip() or "搞怪"
            res = [f for f, i in self.data.items() if kw in i.get("tags", "")]
            if not res: res = list(self.data.keys())
            if res:
                await event.send(Image.fromFileSystem(os.path.join(self.img_dir, random.choice(res))))
            return

        # 找图片URL
        img_url = None
        if hasattr(msg_obj, "message"):
            for comp in msg_obj.message:
                if isinstance(comp, Image): img_url = comp.url; break
        if not img_url and hasattr(msg_obj, "message_chain"):
             for comp in msg_obj.message_chain:
                if isinstance(comp, Image): img_url = comp.url; break

        if not img_url: return

        # 手动存图
        if "记住" in msg or "存图" in msg:
            tags = msg.replace("记住", "").replace("存图", "").strip() or "未分类"
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url) as r:
                    if r.status == 200:
                        content = await r.read()
                        md5 = self.calculate_md5(content)
                        fn = f"{int(time.time())}.jpg"
                        with open(os.path.join(self.img_dir, fn), 'wb') as f: f.write(content)
                        self.data[fn] = {"tags": tags, "source": "manual", "hash": md5}
                        self.save_data()
                        print(f"✅ 手动收录: {tags}")
            return
        
        # 触发 AI 识图 (冷却检查)
        cooldown = self.local_config.get("pick_cooldown", 30)
        if time.time() - self.last_pick_time > cooldown:
            asyncio.create_task(self.ai_evaluate_image(img_url, context_text=msg))
