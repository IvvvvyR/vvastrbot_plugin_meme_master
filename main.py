import os
import json
import random
import asyncio
import time
import hashlib
import aiohttp
import difflib
import traceback # 引入这个，让报错现原形
from aiohttp import web

from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter
from astrbot.api.event.filter import EventMessageType
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.message.components import Image, Plain

print("DEBUG: main.py 正在被读取...") # 探头1

@register("vv_meme_master", "MemeMaster", "AI智能表情包", "15.1.0")
class MemeMaster(Star):
    def __init__(self, context: Context, config: dict = None):
        print("DEBUG: MemeMaster 插件正在初始化...") # 探头2
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
            print(f"ERROR: Web后台启动异常: {e}")

    # ==============================================================
    # 逻辑部分 1：递小抄
    # ==============================================================
    @filter.event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        # 探头3：只要收到消息，这里必须打印！
        print(f"DEBUG: [MemeMaster] 收到消息: {event.message_str}")
        
        try:
            img_url = self._get_img_url(event)
            
            # --- 图片自动存图逻辑 ---
            if img_url and "/存图" not in event.message_str:
                print("DEBUG: 检测到图片，准备鉴赏...")
                cooldown = self.local_config.get("auto_save_cooldown", 60)
                if time.time() - self.last_auto_save_time > cooldown:
                    asyncio.create_task(self.ai_evaluate_image(img_url, event.message_str))
                else:
                    print("DEBUG: 自动存图冷却中")
                return

            # --- 文字发图逻辑 ---
            if not img_url:
                prob = self.local_config.get("reply_prob", 100)
                rand_val = random.randint(1, 100)
                if rand_val > prob:
                    print(f"DEBUG: 概率未命中 ({rand_val} > {prob})")
                    return 

                descriptions = self.get_all_descriptions()
                if not descriptions:
                    print("DEBUG: 库里没有表情包描述，无法工作")
                    return
                
                display_list = descriptions if len(descriptions) <= 50 else random.sample(descriptions, 50)
                menu_text = "、".join(display_list)
                
                print("DEBUG: 正在注入小抄...")
                system_injection = f"\n\n[System Hint]\nAvailable Memes: [{menu_text}]\nUse 'MEME_TAG: content' to send."
                
                # 强行注入
                if event.message_str is None:
                    event.message_str = ""
                event.message_str += system_injection
                print("DEBUG: 小抄注入完成！")

        except Exception:
            # 探头4：如果有错，打印出来！
            print(f"ERROR in on_message: {traceback.format_exc()}")

    # ==============================================================
    # 逻辑部分 2：发图执行
    # ==============================================================
    @filter.on_decorating_result()
    async def on_decorate(self, event: AstrMessageEvent):
        try:
            result = event.get_result()
            if not result:
                return
            
            text = ""
            try:
                if isinstance(result, list):
                    for comp in result:
                        if isinstance(comp, Plain):
                            text += comp.text
                elif hasattr(result, "message_str") and result.message_str:
                    text = result.message_str
                elif hasattr(result, "chain") and result.chain:
                    for comp in result.chain:
                        if isinstance(comp, Plain):
                            text += comp.text
                else:
                    text = str(result)
            except:
                return

            if "MEME_TAG:" in text:
                print(f"DEBUG: 检测到 AI 发图指令: {text}")
                try:
                    parts = text.split("MEME_TAG:")
                    chat_content = parts[0].strip()
                    selected_desc = parts[1].strip().split('\n')[0]
                    
                    img_path = self.find_best_match(selected_desc)
                    
                    if img_path:
                        print(f"🎯 AI发图成功: {selected_desc}")
                        chain = [Plain(chat_content + "\n"), Image.fromFileSystem(img_path)]
                        event.set_result(chain)
                    else:
                        print(f"DEBUG: 未找到匹配图片: {selected_desc}")
                        event.set_result([Plain(chat_content)])
                except Exception as e:
                    print(f"ERROR in MEME_TAG parsing: {e}")
        except:
            pass

    # ==============================================================
    # 逻辑部分 3：AI 自动鉴赏
    # ==============================================================
    async def ai_evaluate_image(self, img_url, context_text=""):
        print("DEBUG: 开始 AI 鉴赏...")
        try:
            self.last_auto_save_time = time.time()
            provider = self.context.get_using_provider()
            if not provider:
                print("DEBUG: 没有可用的 AI Provider")
                return

            prompt = f"""
你正在帮我整理一个 QQ 表情包素材库。

请判断这张图片是否“值得被保存”，
作为未来聊天中可能会使用的表情包素材。
配文是：“{context_text}”。

判断时请注意：
- 这是一个偏二次元 / meme 使用环境
- 常见来源包括：chiikawa、这狗、线条小狗、多栋、猫meme 等
- 不要过度严肃，也不要把普通照片当成表情包

如果这张图不适合做表情包，请只回复：
NO

如果适合，请严格按下面格式回复（不要多余内容）：

YES
<名称>:<一句自然语言解释这个表情包在什么语境下使用>

规则：
1. 如果你能明确判断这是某个常见 IP、角色或 meme 系列，
   请直接使用大家普遍认得的名字作为「名称」
   例如：chiikawa、这狗、线条小狗、多栋、猫meme
2. 如果无法确定具体 IP，不要强行猜测，
   请使用一个简短的情绪或语气概括作为「名称」
3. 冒号后必须是一句完整、自然的“使用说明”，
   描述人在什么情况下会用这个表情包
"""
            # 这里省略 Prompt 文本以节省空间，功能不变
            
            resp = await provider.text_chat(prompt, session_id=None, image_urls=[img_url])
            content = (getattr(resp, "completion_text", None) or getattr(resp, "text", "")).strip()

            print(f"DEBUG: AI 鉴赏回复: {content}")

            if content.startswith("YES"):
                lines = content.splitlines()
                tag = ""
                for line in lines:
                    if ":" in line or "：" in line:
                        tag = line.strip()
                        break
                if not tag and len(lines) >= 2:
                    tag = lines[1].strip()

                if tag:
                    print(f"🖤 [自动进货] {tag}")
                    await self._save_image_file(img_url, tag, "auto")
        except Exception as e:
            print(f"ERROR in ai_evaluate_image: {e}")

    # ==============================================================
    # Web 后台部分
    # ==============================================================
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
            print(f"DEBUG: WebUI started on port {port}")
        except Exception as e:
            print(f"ERROR starting WebUI: {e}")

    async def handle_index(self, r):
        p = os.path.join(self.base_dir, "index.html")
        if not os.path.exists(p):
            return web.Response(text="index missing", status=404)
        with open(p, "r", encoding="utf-8") as f:
            return web.Response(text=f.read().replace("{{MEME_DATA}}", json.dumps(self.data)), content_type="text/html")

    async def handle_upload(self, r):
        try:
            reader = await r.multipart()
            file_data = None
            filename = None
            tags_text = "未分类"

            while True:
                part = await reader.next()
                if part is None: break
                
                if part.name == "file":
                    filename = part.filename
                    file_data = await part.read()
                elif part.name == "tags":
                    val = await part.text()
                    if val and val.strip(): tags_text = val.strip()

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
            try: os.remove(os.path.join(self.img_dir, fn))
            except: pass
            del self.data[fn]
            self.save_data()
        return web.Response(text="ok")
        
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
        d = await r.json()
        if d.get("filename") in self.data:
            self.data[d.get("filename")]["tags"] = d.get("tags")
            self.save_data()
        return web.Response(text="ok")

    async def handle_get_config(self, r): return web.json_response(self.local_config)
    async def handle_update_config(self, r): 
        self.local_config.update(await r.json())
        self.save_config()
        return web.Response(text="ok")

    # ================== 工具函数 ==================
    def get_all_descriptions(self):
        if not self.data: return []
        return [info.get("tags", "") for info in self.data.values()]

    def find_best_match(self, query):
        best_file = None
        best_ratio = 0.0
        for filename, info in self.data.items():
            tags = info.get("tags", "")
            ratio = difflib.SequenceMatcher(None, query, tags).ratio()
            if query in tags: ratio += 0.5
            if ratio > best_ratio:
                best_ratio = ratio
                best_file = filename
        if best_ratio > 0.1 and best_file:
            return os.path.join(self.img_dir, best_file)
        return None

    @filter.command("存图")
    async def save_cmd(self, event: AstrMessageEvent):
        tags = event.message_str.replace("存图", "").strip()
        img_url = self._get_img_url(event)
        if not img_url: return await event.send("请回复图片")
        if not tags: return await event.send("❌ 请输入描述")
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
        except: pass

    def _get_img_url(self, event):
        try:
            msg_obj = event.message_obj
            if hasattr(msg_obj, "message"):
                for comp in msg_obj.message:
                    if isinstance(comp, Image): return comp.url
            if hasattr(msg_obj, "message_chain"):
                for comp in msg_obj.message_chain:
                    if isinstance(comp, Image): return comp.url
        except: return None
        return None

    def load_config(self):
        default = {"web_port": 5000, "reply_prob": 100, "auto_save_cooldown": 60}
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r") as f: default.update(json.load(f))
        except: pass
        return default
    
    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.local_config, f, indent=2)
        except: pass

    def load_data(self):
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, "r") as f: return json.load(f)
        except: pass
        return {}

    def save_data(self):
        try:
            with open(self.data_file, "w") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except: pass
