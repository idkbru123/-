import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os
import json
import re
import requests
import asyncio
import time
import psutil
from datetime import datetime, timedelta
import io
import base64
import subprocess
import sys

# --- 1. WEB SERVER ẢO GIỮ BOT ONLINE TRÊN RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot đang chạy online!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 2. CẤU HÌNH VÀ TÍNH NĂNG BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# LẤY PROXY TỪ RENDER ĐỂ VƯỢT RATE LIMIT
PROXY_URL = os.environ.get("PROXY_URL")

# Truyền proxy vào Bot (Nếu trên Render không có biến PROXY_URL, bot sẽ chạy bằng IP gốc)
bot = commands.Bot(command_prefix="!", intents=intents, proxy=PROXY_URL)
start_time = time.time() # Lưu thời gian bắt đầu chạy bot để tính Uptime

# --- HỆ THỐNG LƯU TRỮ VĨNH VIỄN QUA UPSTASH REDIS ---
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
REDIS_KEY = "bot_data"

# Cấu hình GitHub API cho tính năng tự sửa code
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "idkbru123/-")

# DANH SÁCH ID ĐƯỢC PHÉP DÙNG LỆNH !REPLACECODE
EXTRA_OWNER_IDS = [
    1064147446894174278,
]

def load_data():
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("CẢNH BÁO: Chưa cấu hình UPSTASH_REDIS_REST_URL / TOKEN. Dữ liệu sẽ KHÔNG được lưu!")
        return {}
    try:
        r = requests.get(
            f"{UPSTASH_URL}/get/{REDIS_KEY}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=10
        )
        result = r.json().get("result")
        if result:
            parsed = json.loads(result)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if isinstance(parsed, dict):
                return parsed
    except Exception as e:
        print(f"Lỗi khi load data từ Upstash: {e}")
    return {}

def save_data(data):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return
    try:
        requests.post(
            f"{UPSTASH_URL}/set/{REDIS_KEY}",
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type": "application/json"
            },
            data=json.dumps(json.dumps(data)),
            timeout=10
        )
    except Exception as e:
        print(f"Lỗi khi save data lên Upstash: {e}")

auto_delete_dict = load_data()

# Các bộ đếm Anti-Spam
tag_spam_tracker = {}
msg_spam_tracker = {} 
slow_spam_tracker = {} 
blocked_spam_count = 0 # Đếm tổng số spam đã chặn

# --- HÀM XÓA TIN NHẮN AN TOÀN (TỐI ƯU CHỐNG 429 RATE LIMIT) ---
async def safe_delete(message, retry_on_ratelimit=True):
    try:
        await message.delete()
        return True
    except discord.NotFound:
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException as e:
        # Tối ưu: Nếu gặp 429, không cố chấp spam request xóa nữa mà thả lỏng để bảo vệ API
        if e.status == 429:
            if retry_on_ratelimit:
                retry_after = getattr(e, "retry_after", 2) or 2
                await asyncio.sleep(retry_after)
                try:
                    await message.delete()
                    return True
                except:
                    return False
        return False

# HÀM KIỂM TRA QUYỀN CAO
def is_high_permission():
    async def predicate(ctx):
        p = ctx.author.guild_permissions
        if p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild:
            return True
        raise commands.CheckFailure("Lỗi thiếu quyền")
    return commands.check(predicate)

# HÀM KIỂM TRA QUYỀN SỬA CODE (Chủ bot + Danh sách ID phụ)
async def check_replace_permission(ctx):
    is_owner = await ctx.bot.is_owner(ctx.author)
    if is_owner:
        return True
    if ctx.author.id in EXTRA_OWNER_IDS:
        return True
    raise commands.CheckFailure("Chỉ Chủ bot hoặc người được cấp quyền mới dùng được lệnh này!")

# HÀM QUY ĐỔI CHUỖI THỜI GIAN THÀNH GIÂY
def parse_time_to_seconds(time_str):
    if isinstance(time_str, int):
        return time_str * 60

    time_str = str(time_str).lower().replace(" ", "")
    if time_str.isdigit():
        return int(time_str) * 60 

    time_str = time_str.replace("phút", "p").replace("phut", "p")
    time_str = time_str.replace("giây", "s").replace("giay", "s")
    time_str = time_str.replace("giờ", "h").replace("gio", "h")

    total_seconds = 0
    matches = re.findall(r'(\d+)([hpmsc])', time_str)
    if not matches:
        return -1

    for val, unit in matches:
        val = int(val)
        if unit == 'h': total_seconds += val * 3600
        elif unit in ['p', 'm']: total_seconds += val * 60
        elif unit in ['s', 'c']: total_seconds += val

    return total_seconds

# HÀM GỬI NHẬT KÝ (MOD LOGS) - Tối ưu 429
async def send_mod_log(guild, title, description, color=discord.Color.red()):
    guild_id_str = str(guild.id)
    log_channel_id = auto_delete_dict.get(guild_id_str, {}).get("settings", {}).get("log_channel")

    if log_channel_id:
        channel = guild.get_channel(log_channel_id)
        if channel:
            embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now())
            embed.set_footer(text="Hệ thống Vệ sĩ tự động")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                if e.status == 429:
                    pass # Bỏ qua log nếu đang bị quá tải API

# HÀM TRÍCH XUẤT BẢNG PHÂN QUYỀN CỦA KÊNH/ROLE (PHỤC VỤ BACKUP)
def serialize_overwrites(channel):
    overwrites_data = []
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
            allow_pair, deny_pair = overwrite.pair()
            overwrites_data.append({
                "role_name": target.name,
                "allow": allow_pair.value,
                "deny": deny_pair.value
            })
    return overwrites_data

# TỰ ĐỘNG XÓA LỆNH BOT
@bot.before_invoke
async def delete_command_message(ctx):
    if ctx.message:
        await safe_delete(ctx.message, retry_on_ratelimit=False)

# --- HÀM TẠO GIAO DIỆN EMBED CHO SETTING ---
def get_setting_embed(guild_id_str):
    settings = auto_delete_dict[guild_id_str]["settings"]
    status = "🟢 ĐANG BẬT" if settings.get("anti_spam", False) else "🔴 ĐANG TẮT"

    mute_display = settings.get('mute_time', '1p')
    if isinstance(mute_display, int): mute_display = f"{mute_display} phút"

    log_ch_id = settings.get("log_channel")
    log_display = f"<#{log_ch_id}>" if log_ch_id else "❌ Chưa cài đặt"

    fake_bot_status = "🟢 ĐANG BẬT" if settings.get("block_fake_bot", False) else "🔴 ĐANG TẮT"

    embed = discord.Embed(title="⚙️ BẢNG ĐIỀU KHIỂN CHỐNG SPAM", color=discord.Color.blue())
    embed.set_image(url="https://cdn.discordapp.com/attachments/1344684960878952590/1562683883575845004/attachment.gif")
    embed.add_field(
        name="1. Mức phạt chung", 
        value=f"• Thời gian Mute: **{mute_display}**", 
        inline=False
    )
    embed.add_field(
        name="2. Chống Ghost Ping (!autodelete)", 
        value=f"• Giới hạn spam tag: **{settings['tag_limit']} lần** / 60 giây", 
        inline=False
    )
    embed.add_field(
        name="3. Chống Spam Tin nhắn (!nospam)", 
        value=f"• Trạng thái tính năng: **{status}**\n• Mức 1 (Cảnh cáo): **{settings.get('warn_limit', 5)} tin nhắn** / 10s\n• Mức 2 (Bị Mute): **{settings['msg_limit']} tin nhắn** / 10s\n• Chống copy-paste: **Xóa từ lần trùng thứ 5 (Staff chỉ bị xóa)**", 
        inline=False
    )
    embed.add_field(
        name="4. Chặn Bot/Webhook Ma (!blockfakebot)", 
        value=f"• Trạng thái: **{fake_bot_status}**\n• Diệt ngay lập tức Bot/Webhook không có Role hợp lệ trong Server, block thẳng vào danh sách đen.", 
        inline=False
    )
    embed.add_field(
        name="5. Nhật ký Vệ sĩ (Mod Logs)", 
        value=f"• Kênh xuất Log: **{log_display}**\n👉 Dùng lệnh: `!setlogg #kenh` để cài đặt.", 
        inline=False
    )
    embed.set_footer(text="Bấm vào các nút bên dưới để tùy chỉnh")
    return embed

# --- CLASS TẠO HỘP THOẠI NHẬP SỐ (MODAL) ---
class SettingModal(discord.ui.Modal):
    def __init__(self, setting_key, title_text, placeholder_text, view_instance):
        super().__init__(title=title_text)
        self.setting_key = setting_key
        self.view_instance = view_instance

        self.value_input = discord.ui.TextInput(
            label="Nhập thông số mới:",
            style=discord.TextStyle.short,
            placeholder=placeholder_text,
            required=True
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        val = self.value_input.value
        guild_id_str = str(interaction.guild.id)

        if self.setting_key == "mute_time":
            if parse_time_to_seconds(val) <= 0:
                return await interaction.response.send_message("❌ Định dạng không hợp lệ! Vui lòng dùng số kèm s, p, h.", ephemeral=True)
            auto_delete_dict[guild_id_str]["settings"][self.setting_key] = val.lower().strip()
        else:
            if not val.isdigit() or int(val) < 1:
                return await interaction.response.send_message("❌ Vui lòng nhập một số hợp lệ lớn hơn 0!", ephemeral=True)

            new_val = int(val)
            current_settings = auto_delete_dict[guild_id_str]["settings"]

            if self.setting_key == "warn_limit" and new_val >= current_settings.get("msg_limit", 10):
                return await interaction.response.send_message("❌ Lỗi: Mức CẢNH CÁO phải NHỎ HƠN Mức PHẠT (Mute)!", ephemeral=True)
            if self.setting_key == "msg_limit" and new_val <= current_settings.get("warn_limit", 5):
                return await interaction.response.send_message("❌ Lỗi: Mức PHẠT (Mute) phải LỚN HƠN Mức CẢNH CÁO!", ephemeral=True)

            auto_delete_dict[guild_id_str]["settings"][self.setting_key] = new_val

        save_data(auto_delete_dict)
        new_embed = get_setting_embed(guild_id_str)
        await interaction.response.edit_message(embed=new_embed, view=self.view_instance)
        await interaction.followup.send(f"✅ Đã cập nhật thành công!", ephemeral=True)

# --- CLASS TẠO NÚT BẤM CHO SETTING ---
class SettingView(discord.ui.View):
    def __init__(self, guild_id_str, original_author):
        super().__init__(timeout=300)
        self.guild_id_str = guild_id_str
        self.original_author = original_author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_author:
            await interaction.response.send_message(
                f"❌ Bảng điều khiển này đang được sử dụng bởi {self.original_author.mention}.", 
                ephemeral=True
            )
            return False

        p = interaction.user.guild_permissions
        if not (p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild):
            await interaction.response.send_message("❌ Bạn cần có quyền Quản trị / Staff!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Bật/Tắt", style=discord.ButtonStyle.success, emoji="🛡️")
    async def toggle_spam(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_status = auto_delete_dict[self.guild_id_str]["settings"].get("anti_spam", False)
        auto_delete_dict[self.guild_id_str]["settings"]["anti_spam"] = not current_status
        save_data(auto_delete_dict)

        button.style = discord.ButtonStyle.danger if current_status else discord.ButtonStyle.success
        await interaction.response.edit_message(embed=get_setting_embed(self.guild_id_str), view=self)

    @discord.ui.button(label="TG Mute", style=discord.ButtonStyle.primary, emoji="⏱️")
    async def edit_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("mute_time", "Sửa Thời Gian Mute", "VD: 30s, 1 phút 20 giây", self))

    @discord.ui.button(label="Mức Tag", style=discord.ButtonStyle.primary, emoji="🏷️")
    async def edit_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("tag_limit", "Giới hạn Spam Tag (Lần)", "Ví dụ: 5", self))

    @discord.ui.button(label="Mức Cảnh Cáo", style=discord.ButtonStyle.primary, emoji="⚠️")
    async def edit_warn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("warn_limit", "Mức Nhắc Nhở Spam (trong 10s)", "Ví dụ: 5", self))

    @discord.ui.button(label="Mức Phạt (Mute)", style=discord.ButtonStyle.danger, emoji="💬")
    async def edit_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("msg_limit", "Mức Phạt Mute/Nuke (trong 10s)", "Ví dụ: 10", self))

    @discord.ui.button(label="Cài Kênh Log", style=discord.ButtonStyle.secondary, emoji="📓")
    async def edit_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("👉 Vui lòng ra ngoài kênh chat và gõ lệnh:\n`!setlogg #tên-kênh` để cài đặt\nHoặc gõ `!setlogg` để tắt.", ephemeral=True)

    @discord.ui.button(label="Chặn Bot/Webhook Ma", style=discord.ButtonStyle.success, emoji="👻")
    async def toggle_fake_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_status = auto_delete_dict[self.guild_id_str]["settings"].get("block_fake_bot", False)
        auto_delete_dict[self.guild_id_str]["settings"]["block_fake_bot"] = not current_status
        save_data(auto_delete_dict)

        button.style = discord.ButtonStyle.danger if current_status else discord.ButtonStyle.success
        await interaction.response.edit_message(embed=get_setting_embed(self.guild_id_str), view=self)

    @discord.ui.button(label="Đóng UI", style=discord.ButtonStyle.secondary, emoji="❌")
    async def close_ui(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_delete(interaction.message)

# --- CLASS TẠO NÚT BẤM CHO STATS ---
class StatsView(discord.ui.View):
    def __init__(self, original_author):
        super().__init__(timeout=180)
        self.original_author = original_author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.original_author:
            await interaction.response.send_message(
                f"❌ Bảng này được gọi bởi {self.original_author.mention}.", 
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Đóng UI", style=discord.ButtonStyle.danger, emoji="❌")
    async def close_ui(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_delete(interaction.message)

@bot.event
async def on_ready():
    print(f'Bot {bot.user} đã Online và tải xong dữ liệu!')

@bot.event
async def on_message(message):
    global blocked_spam_count
    
    if message.author == bot.user or not message.guild:
        return

    guild_id_str = str(message.guild.id)

    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}
    elif "settings" not in auto_delete_dict[guild_id_str]:
        auto_delete_dict[guild_id_str]["settings"] = {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}
    else:
        if "warn_limit" not in auto_delete_dict[guild_id_str]["settings"]:
            auto_delete_dict[guild_id_str]["settings"]["warn_limit"] = 5
        if "msg_limit" not in auto_delete_dict[guild_id_str]["settings"] or auto_delete_dict[guild_id_str]["settings"]["msg_limit"] <= auto_delete_dict[guild_id_str]["settings"]["warn_limit"]:
            auto_delete_dict[guild_id_str]["settings"]["msg_limit"] = auto_delete_dict[guild_id_str]["settings"]["warn_limit"] + 5
        if "log_channel" not in auto_delete_dict[guild_id_str]["settings"]:
            auto_delete_dict[guild_id_str]["settings"]["log_channel"] = None
        if "block_fake_bot" not in auto_delete_dict[guild_id_str]["settings"]:
            auto_delete_dict[guild_id_str]["settings"]["block_fake_bot"] = False

    server_data = auto_delete_dict[guild_id_str]
    server_settings = server_data["settings"]
    author_id = message.author.id

    mute_time_raw = server_settings.get("mute_time", "1p")
    mute_seconds = parse_time_to_seconds(mute_time_raw)
    if mute_seconds <= 0: mute_seconds = 60

    # 0. HỆ THỐNG TIÊU DIỆT BOT/WEBHOOK MA
    if server_settings.get("block_fake_bot", False):
        if message.author.bot or message.webhook_id:
            is_fake = True
            
            author_member = message.guild.get_member(message.author.id)
            if author_member and len(author_member.roles) > 1:
                is_fake = False

            if is_fake:
                blocked_spam_count += 1
                await safe_delete(message)

                target_name = message.author.name.lower()

                if target_name not in auto_delete_dict[guild_id_str]["names"]:
                    auto_delete_dict[guild_id_str]["names"].append(target_name)
                    save_data(auto_delete_dict)

                    try:
                        await message.channel.send(
                            f"🛡️ **Anti-Nuke:** Đã tiêu diệt Bot/Webhook lạ (`{message.author.name}`) do không có Role hợp lệ!",
                            delete_after=15
                        )
                    except discord.HTTPException:
                        pass

                    await send_mod_log(
                        message.guild,
                        "💀 Tiêu Diệt Bot/Webhook Ma (Tức Thì)",
                        f"**Đối tượng:** `{message.author.name}`\n**Hành động:** Xóa lập tức & Block vĩnh viễn\n**Lý do:** Kẻ gửi không có Role hợp lệ trong Server (Webhooks/Bot lạ).",
                        discord.Color.brand_red()
                    )
                return 

    # KIỂM TRA QUYỀN
    is_staff_or_bot = False
    author_member = message.guild.get_member(message.author.id)
    if author_member:
        if author_member.bot:
            is_staff_or_bot = True 
        else:
            p = author_member.guild_permissions
            if p.administrator or p.manage_messages or p.manage_guild or p.kick_members or p.ban_members:
                is_staff_or_bot = True 

    # 1. HỆ THỐNG CHỐNG SPAM TOÀN DIỆN
    if server_settings.get("anti_spam", False):
        tracker_key = (guild_id_str, author_id)
        now = datetime.now()

        if tracker_key not in msg_spam_tracker:
            msg_spam_tracker[tracker_key] = {"count": 1, "start_time": now}
        else:
            if (now - msg_spam_tracker[tracker_key]["start_time"]).total_seconds() > 10:
                msg_spam_tracker[tracker_key] = {"count": 1, "start_time": now}
            else:
                msg_spam_tracker[tracker_key]["count"] += 1

        current_msg_count = msg_spam_tracker[tracker_key]["count"]
        warn_limit = server_settings["warn_limit"]
        msg_limit = server_settings["msg_limit"]

        if current_msg_count == warn_limit and not is_staff_or_bot:
            if isinstance(message.author, discord.Member) and not message.webhook_id:
                await message.channel.send(f"⚠️ {message.author.mention} **CẢNH CÁO:** Bạn đang gửi tin nhắn quá nhanh!", delete_after=10)
                await send_mod_log(message.guild, "⚠️ Phát hiện tốc độ cao", f"**Đối tượng:** {message.author.mention}\n**Hành động:** Đã nhắc nhở\n**Lý do:** Đạt mức cảnh cáo ({warn_limit} tin/10s)", discord.Color.yellow())

        elif current_msg_count >= msg_limit:
            blocked_spam_count += 1
            if is_staff_or_bot:
                await safe_delete(message)
            else:
                if isinstance(message.author, discord.Member) and not message.webhook_id:
                    try:
                        await message.author.timeout(timedelta(seconds=mute_seconds), reason="Phớt lờ cảnh cáo Spam")
                        await message.channel.send(f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do tiếp tục spam sau khi bị cảnh cáo!", delete_after=15)
                        await send_mod_log(message.guild, "🚫 Phạt Spam Nhanh", f"**Đối tượng:** {message.author.mention}\n**Hành động:** Mute {mute_time_raw}\n**Lý do:** spam chat {msg_limit} tin/10s", discord.Color.orange())
                    except discord.Forbidden:
                        pass
                else:
                    target_name = message.author.name.lower()
                    if target_name not in auto_delete_dict[guild_id_str]["names"]:
                        auto_delete_dict[guild_id_str]["names"].append(target_name)
                        save_data(auto_delete_dict)
                        try:
                            await message.channel.purge(limit=msg_limit + 5, check=lambda m: m.author.id == author_id or (m.webhook_id and m.webhook_id == message.webhook_id))
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                        await message.channel.send(f"🛡️ **Anti-Nuke:** Phát hiện Webhook lạ (`{message.author.name}`) đang spam phá hoại! Đã khóa vào Danh Sách Đen.", delete_after=15)
                        await send_mod_log(message.guild, "💀 Tiêu Diệt Nuke Bot/Webhook", f"**Đối tượng:** `{target_name}`\n**Hành động:** Quét sạch chat & Block Vĩnh viễn\n**Lý do:** Xả spam tốc độ cao", discord.Color.brand_red())
            msg_spam_tracker[tracker_key] = {"count": 0, "start_time": now}

        msg_content = message.content.strip().lower()
        if msg_content: 
            if tracker_key in slow_spam_tracker:
                tracker = slow_spam_tracker[tracker_key]
                if tracker["content"] == msg_content and (now - tracker["last_time"]).total_seconds() < 60:
                    tracker["count"] += 1

                    if tracker["count"] >= 5:
                        blocked_spam_count += 1
                        await safe_delete(message)

                        if not is_staff_or_bot:
                            if tracker["first_duplicate_time"] is None:
                                tracker["first_duplicate_time"] = now 
                            else:
                                if (now - tracker["first_duplicate_time"]).total_seconds() >= 20:
                                    if isinstance(message.author, discord.Member) and not message.webhook_id:
                                        try:
                                            await message.author.timeout(timedelta(seconds=mute_seconds), reason="Spam lặp một nội dung quá lâu")
                                            await message.channel.send(f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do cố tình spam lặp đi lặp lại cùng một nội dung liên tục!", delete_after=15)
                                            await send_mod_log(message.guild, "🚫 Phạt Lặp Nội Dung", f"**Đối tượng:** {message.author.mention}\n**Hành động:** Mute {mute_time_raw}\n**Lý do:** Copy-paste liên tục", discord.Color.orange())
                                        except discord.Forbidden:
                                            pass
                                    else:
                                        target_name = message.author.name.lower()
                                        if target_name not in auto_delete_dict[guild_id_str]["names"]:
                                            auto_delete_dict[guild_id_str]["names"].append(target_name)
                                            save_data(auto_delete_dict)
                                            await message.channel.send(f"🛡️ **Anti-Nuke:** Khóa Webhook lạ (`{message.author.name}`) do spam lặp chữ!", delete_after=15)
                                            await send_mod_log(message.guild, "💀 Tiêu Diệt Nuke Bot/Webhook", f"**Đối tượng:** `{target_name}`\n**Hành động:** Block Vĩnh viễn\n**Lý do:** Spam lặp nội dung", discord.Color.brand_red())

                                    slow_spam_tracker[tracker_key] = {"content": "", "last_time": now, "count": 0, "first_duplicate_time": None}
                                    return 

                        tracker["last_time"] = now
                        return 
                    else:
                        tracker["last_time"] = now
                else:
                    slow_spam_tracker[tracker_key] = {"content": msg_content, "last_time": now, "count": 1, "first_duplicate_time": None}
            else:
                slow_spam_tracker[tracker_key] = {"content": msg_content, "last_time": now, "count": 1, "first_duplicate_time": None}

    # 2. TÍNH NĂNG TỰ ĐỘNG XÓA VÀ CHỐNG GHOST PING CỦA DANH SÁCH ĐEN
    blacklisted_ids = server_data["ids"]
    blacklisted_names = server_data["names"]
    is_blacklisted = False
    author_name = message.author.name.lower()
    display_name = getattr(message.author, 'display_name', '').lower()

    if author_id in blacklisted_ids or (message.webhook_id and message.webhook_id in blacklisted_ids):
        is_blacklisted = True
    elif author_name in blacklisted_names or display_name in blacklisted_names:
        is_blacklisted = True

    if is_blacklisted:
        valid_mentions = [user for user in message.mentions if user != message.author]
        if valid_mentions:
            tracker_key = (guild_id_str, author_id)
            now = datetime.now()

            if tracker_key not in tag_spam_tracker:
                tag_spam_tracker[tracker_key] = {"count": 1, "last_time": now}
            else:
                if (now - tag_spam_tracker[tracker_key]["last_time"]).total_seconds() > 60:
                    tag_spam_tracker[tracker_key] = {"count": 1, "last_time": now}
                else:
                    tag_spam_tracker[tracker_key]["count"] += 1
                    tag_spam_tracker[tracker_key]["last_time"] = now

            current_count = tag_spam_tracker[tracker_key]["count"]
            tag_limit = server_settings["tag_limit"]

            if current_count >= tag_limit:
                blocked_spam_count += 1
                if isinstance(message.author, discord.Member) and not is_staff_or_bot:
                    try:
                        await message.author.timeout(timedelta(seconds=mute_seconds), reason="Spam tag")
                        await message.channel.send(
                            f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do cố tình spam tag quá {tag_limit} lần!",
                            delete_after=15
                        )
                        await send_mod_log(message.guild, "🚫 Phạt Spam Tag", f"**Đối tượng:** {message.author.mention}\n**Hành động:** Mute {mute_time_raw}\n**Lý do:** Tag bừa bãi quá {tag_limit} lần/phút", discord.Color.orange())
                    except discord.Forbidden:
                        pass
                tag_spam_tracker[tracker_key] = {"count": 0, "last_time": now}
            else:
                pinged_users = ", ".join([user.mention for user in valid_mentions])
                sender_display = message.author.mention if hasattr(message.author, 'mention') else f"**{message.author.name}**"
                try:
                    await message.channel.send(
                        f"⚠️ **Ghost Ping!** {sender_display} vừa tag {pinged_users} ({current_count}/{tag_limit} lần).",
                        delete_after=10
                    )
                except discord.HTTPException:
                    pass
        blocked_spam_count += 1
        await safe_delete(message)

    await bot.process_commands(message)


# --- 3. LỆNH ĐIỀU KHIỂN (ĐÃ THÊM COOLDOWN BẢO VỆ API) ---

@bot.command(name="addowner")
@commands.is_owner()
@commands.cooldown(1, 2, commands.BucketType.user)
async def add_owner(ctx, member: discord.Member):
    if member.id in EXTRA_OWNER_IDS:
        return await ctx.send(f"⚠️ {member.mention} đã có trong danh sách từ trước rồi!")
    
    EXTRA_OWNER_IDS.append(member.id)
    await ctx.send(f"✅ Đã cấp quyền sử dụng `!replacecode` cho {member.mention}!")

@bot.command(name="removeowner")
@commands.is_owner()
@commands.cooldown(1, 2, commands.BucketType.user)
async def remove_owner(ctx, member: discord.Member):
    if member.id in EXTRA_OWNER_IDS:
        EXTRA_OWNER_IDS.remove(member.id)
        return await ctx.send(f"✅ Đã thu hồi quyền của {member.mention}!")
    await ctx.send(f"❌ {member.mention} không nằm trong danh sách được cấp quyền phụ.")

@bot.command(name="replacecode")
@commands.check(check_replace_permission)
@commands.cooldown(1, 5, commands.BucketType.user)
async def replace_code(ctx, *, args: str):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return await ctx.send("❌ Chưa cấu hình `GITHUB_TOKEN` hoặc `GITHUB_REPO` trên Render!")

    if "|||" not in args:
        return await ctx.send("⚠️ Cú pháp: `!replacecode <code cũ hoặc ALL> ||| <code mới>`")

    old_code, new_code = args.split("|||", 1)
    old_code = old_code.strip()
    new_code = new_code.strip()

    msg = await ctx.send("🔄 Đang lấy dữ liệu file `bot.py` từ GitHub...")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/bot.py"

    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        return await msg.edit(content=f"❌ Lỗi khi tải file từ GitHub: `{r.json().get('message')}`")

    file_data = r.json()
    sha = file_data["sha"]
    current_content = base64.b64decode(file_data["content"]).decode('utf-8')

    if old_code.upper() in ["ALL", "*", "CLEAR", "FULL"]:
        updated_content = new_code
    else:
        if old_code not in current_content:
            return await msg.edit(content="❌ Không tìm thấy đoạn code cũ cần thay thế trong file `bot.py`!")
        updated_content = current_content.replace(old_code, new_code, 1)

    try:
        compile(updated_content, filename='bot.py', mode='exec')
    except SyntaxError as e:
        error_line = e.text.strip() if e.text else 'Không xác định'
        return await msg.edit(content=f"❌ **LỖI CÚ PHÁP (SYNTAX ERROR)!**\nCode bị lỗi nên bot **ĐÃ CHẶN LẠI**, không commit lên GitHub.\n\n• **Chi tiết lỗi:** `{e.msg}`\n• **Dòng lỗi:** `{e.lineno}`\n• **Đoạn mã lỗi:** `{error_line}`")

    await msg.edit(content="🧪 **Đang chạy thử code mới trong môi trường cách ly (5 giây)...**")
    
    test_filename = "temp_test_bot.py"
    with open(test_filename, "w", encoding="utf-8") as f:
        f.write(updated_content)

    try:
        process = subprocess.Popen(
            [sys.executable, test_filename],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        await asyncio.sleep(5)
        
        if process.poll() is not None:
            _, stderr = process.communicate()
            if os.path.exists(test_filename): os.remove(test_filename)
            return await msg.edit(content=f"❌ **LỖI RUNTIME/KHỞI CHẠY (BOT SẬP KHI CHẠY THỬ)!**\nĐã chặn commit để bảo vệ Bot.\n\n```text\n{stderr[:1500]}\n```")
        else:
            process.terminate()
            process.wait()

    except Exception as e:
        if os.path.exists(test_filename): os.remove(test_filename)
        return await msg.edit(content=f"❌ **Lỗi khi thực thi test:** `{e}`")

    if os.path.exists(test_filename):
        os.remove(test_filename)

    await msg.edit(content="✅ **Code chạy thử an toàn!** Đang tiến hành commit lên GitHub...")
    encoded_updated_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')
    data = {
        "message": f"🤖 Discord Bot: Cập nhật code an toàn từ {ctx.author.name}",
        "content": encoded_updated_content,
        "sha": sha
    }

    put_r = requests.put(url, headers=headers, json=data)
    if put_r.status_code == 200:
        await msg.edit(content="✅ **Đã commit code mới lên GitHub thành công!**\n🚀 Render sẽ tự động bắt đầu Build và Deploy bản cập nhật mới trong 1-2 phút.")
    else:
        await msg.edit(content=f"❌ Lỗi khi commit lên GitHub: `{put_r.json().get('message')}`")

@bot.command(name="cmdhelp")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def cmdhelp(ctx):
    embed = discord.Embed(
        title="📚 Hướng Dẫn Sử Dụng Tính Năng Chống Spam",
        description="Dưới đây là các lệnh để thiết lập hệ thống bảo vệ Server:",
        color=discord.Color.green()
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1344684960878952590/1562683883575845004/attachment.gif")

    embed.add_field(name="🛠️ `!setting`", value="Mở bảng điều khiển chính để chỉnh sửa các mốc giới hạn, thời gian mute và bật/tắt tính năng chống spam.", inline=False)
    embed.add_field(name="🛡️ `!nospam`", value="Lệnh bật/tắt nhanh chế độ chống spam mà không cần mở bảng cài đặt.", inline=False)
    embed.add_field(name="📓 `!setlogg #kênh` / `!setlogg`", value="Thiết lập kênh để nhận thông báo (Mod Logs) mỗi khi bot xử lý vi phạm. Gõ lệnh không kèm kênh để tắt.", inline=False)
    embed.add_field(name="🚨 `!lockdown`", value="Khóa/Mở khóa khẩn cấp Server (Tắt/Bật quyền gửi tin nhắn của `@everyone`). Dùng khi bị raid.", inline=False)
    embed.add_field(name="💾 `!backup` & `!restore`", value="• `!backup`: Sao lưu toàn bộ Role, Danh mục, Kênh chữ/thoại & Phân quyền lên Cloud + gửi file `.json` qua DM.\n• `!restore`: Khôi phục lại toàn bộ cấu trúc Server (đính kèm file `.json` nếu có).", inline=False)
    embed.add_field(name="📊 `!stats` / `!health`", value="Xem báo cáo sức khỏe chi tiết của Bot, độ trễ API, dung lượng RAM và tổng số tin rác đã chặn.", inline=False)
    embed.add_field(name="👻 Tự động xóa & Chống Ghost Ping", value="• `!autodelete <ID/Tên>`: Đưa người dùng vào danh sách đen (tự xóa tin nhắn, cảnh cáo ghost ping).\n• `!stopdelete <ID/Tên>`: Xóa khỏi danh sách đen.\n• `!listdelete`: Xem danh sách đen hiện tại.", inline=False)
    embed.set_footer(text="Hệ thống Vệ sĩ tự động • Chỉ dành cho Staff/Admin")
    await ctx.send(embed=embed)

@bot.command(name="stats", aliases=["health"])
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def stats(ctx):
    uptime_seconds = int(time.time() - start_time)
    uptime_str = str(timedelta(seconds=uptime_seconds))

    ping = round(bot.latency * 1000)
    ping_status = "🟢 Tốt" if ping < 150 else ("🟡 Trung bình" if ping < 300 else "🔴 Kém")

    process = psutil.Process(os.getpid())
    ram_usage = process.memory_info().rss / (1024 * 1024)
    cpu_usage = psutil.cpu_percent()

    guild_count = len(bot.guilds)
    total_members = sum(g.member_count for g in bot.guilds)

    embed = discord.Embed(title="📊 BÁO CÁO SỨC KHỎE HỆ THỐNG", color=discord.Color.teal(), timestamp=datetime.now())
    embed.set_thumbnail(url=bot.user.avatar.url if bot.user.avatar else bot.user.default_avatar.url)
    embed.set_image(url="https://cdn.discordapp.com/attachments/1344684960878952590/1562683883575845004/attachment.gif")

    embed.add_field(name="⚙️ Trạng Thái Kết Nối", value=f"• **Ping Discord:** `{ping}ms` ({ping_status})\n• **Uptime:** `{uptime_str}`\n• **Lưu trữ Cloud:** `🟢 Hoạt động`", inline=False)
    embed.add_field(name="💻 Tài Nguyên Cấu Hình", value=f"• **RAM Usage:** `{ram_usage:.2f} MB`\n• **CPU Usage:** `{cpu_usage}%`\n• **Nền tảng:** `Flask / Render`", inline=False)
    embed.add_field(name="🛡️ Hiệu Suất Bảo Vệ", value=f"• **Phục vụ:** `{guild_count} Server` | `{total_members} Thành viên`\n• **Tổng tin rác đã chặn:** `{blocked_spam_count} tin nhắn`\n• **Anti-Nuke / Anti-Spam:** `🟢 Đang trực`", inline=False)
    embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url)
    
    view = StatsView(ctx.author)
    await ctx.send(embed=embed, view=view)

@bot.command(name="backup")
@is_high_permission()
@commands.cooldown(1, 5, commands.BucketType.user)
async def backup(ctx):
    guild = ctx.guild
    msg = await ctx.send("🔄 Đang quét và sao lưu cấu trúc Server (Kênh, Role, Phân quyền)...")

    backup_data = {
        "guild_name": guild.name,
        "backup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "roles": [],
        "categories": []
    }

    for role in reversed(guild.roles):
        if role.is_default():
            continue
        backup_data["roles"].append({
            "name": role.name,
            "color": role.color.value,
            "permissions": role.permissions.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable
        })

    for category in guild.categories:
        cat_data = {
            "name": category.name,
            "overwrites": serialize_overwrites(category),
            "text_channels": [],
            "voice_channels": []
        }

        for ch in category.channels:
            if isinstance(ch, discord.TextChannel):
                cat_data["text_channels"].append({
                    "name": ch.name,
                    "topic": ch.topic,
                    "slowmode": ch.slowmode_delay,
                    "nsfw": ch.nsfw,
                    "overwrites": serialize_overwrites(ch)
                })
            elif isinstance(ch, discord.VoiceChannel):
                cat_data["voice_channels"].append({
                    "name": ch.name,
                    "user_limit": ch.user_limit,
                    "bitrate": ch.bitrate,
                    "overwrites": serialize_overwrites(ch)
                })

        backup_data["categories"].append(cat_data)

    no_cat_data = {
        "name": "UNCATEGORIZED",
        "overwrites": [],
        "text_channels": [],
        "voice_channels": []
    }
    for ch in guild.channels:
        if ch.category is None:
            if isinstance(ch, discord.TextChannel):
                no_cat_data["text_channels"].append({
                    "name": ch.name,
                    "topic": ch.topic,
                    "slowmode": ch.slowmode_delay,
                    "nsfw": ch.nsfw,
                    "overwrites": serialize_overwrites(ch)
                })
            elif isinstance(ch, discord.VoiceChannel):
                no_cat_data["voice_channels"].append({
                    "name": ch.name,
                    "user_limit": ch.user_limit,
                    "bitrate": ch.bitrate,
                    "overwrites": serialize_overwrites(ch)
                })
    if no_cat_data["text_channels"] or no_cat_data["voice_channels"]:
        backup_data["categories"].append(no_cat_data)

    guild_id_str = str(guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {}
    auto_delete_dict[guild_id_str]["backup"] = backup_data
    save_data(auto_delete_dict)

    json_bytes = json.dumps(backup_data, indent=4, ensure_ascii=False).encode('utf-8')
    file = discord.File(io.BytesIO(json_bytes), filename=f"backup_{guild.id}.json")

    try:
        await ctx.author.send(f"📂 Đây là file backup cấu trúc cho Server **{guild.name}** của bạn:", file=file)
        await msg.edit(content=f"✅ **Đã sao lưu thành công!** Dữ liệu đã lưu lên Cloud. \n📥 File backup `.json` đã được gửi vào **Tin nhắn riêng (DM)** của bạn!")
    except discord.Forbidden:
        await msg.edit(content=f"✅ **Đã sao lưu thành công!** Dữ liệu đã lưu lên Cloud.\n⚠️ {ctx.author.mention} Bot không thể gửi file cho bạn vì bạn đang khóa Tin nhắn riêng (DM). File được đính kèm tạm ở dưới đây:")
        await ctx.send(file=file)

@bot.command(name="restore")
@is_high_permission()
@commands.cooldown(1, 10, commands.BucketType.user)
async def restore(ctx):
    guild = ctx.guild
    guild_id_str = str(guild.id)
    backup_data = None

    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        if attachment.filename.endswith(".json"):
            content = await attachment.read()
            try:
                backup_data = json.loads(content.decode('utf-8'))
            except Exception as e:
                return await ctx.send("❌ File `.json` bị lỗi hoặc không đúng định dạng!")

    if not backup_data:
        backup_data = auto_delete_dict.get(guild_id_str, {}).get("backup")

    if not backup_data:
        return await ctx.send("❌ Không tìm thấy bản sao lưu nào trên Cloud hoặc file `.json` đính kèm!")

    msg = await ctx.send("⚠️ **ĐANG KHÔI PHỤC SERVER...** Quá trình này sẽ diễn ra từ từ để tránh bị Discord hạn chế kết nối (Rate Limit).")

    created_roles = {}
    for r_data in backup_data.get("roles", []):
        existing = discord.utils.get(guild.roles, name=r_data["name"])
        if not existing:
            try:
                new_role = await guild.create_role(
                    name=r_data["name"],
                    color=discord.Color(r_data["color"]),
                    permissions=discord.Permissions(r_data["permissions"]),
                    hoist=r_data["hoist"],
                    mentionable=r_data["mentionable"]
                )
                created_roles[r_data["name"]] = new_role
                await asyncio.sleep(0.5)
            except discord.HTTPException:
                pass
        else:
            created_roles[r_data["name"]] = existing

    def build_overwrites(raw_overwrites):
        ow = {}
        for item in raw_overwrites:
            r_obj = created_roles.get(item["role_name"]) or discord.utils.get(guild.roles, name=item["role_name"])
            if r_obj:
                allow_perms = discord.Permissions(item["allow"])
                deny_perms = discord.Permissions(item["deny"])
                ow[r_obj] = discord.PermissionOverwrite.from_pair(allow_perms, deny_perms)
        return ow

    for cat_data in backup_data.get("categories", []):
        cat_name = cat_data["name"]
        category_obj = None

        if cat_name != "UNCATEGORIZED":
            category_obj = discord.utils.get(guild.categories, name=cat_name)
            if not category_obj:
                try:
                    category_obj = await guild.create_category(
                        name=cat_name,
                        overwrites=build_overwrites(cat_data.get("overwrites", []))
                    )
                    await asyncio.sleep(0.5)
                except discord.HTTPException:
                    pass

        for txt in cat_data.get("text_channels", []):
            if not discord.utils.get(guild.text_channels, name=txt["name"], category=category_obj):
                try:
                    await guild.create_text_channel(
                        name=txt["name"],
                        category=category_obj,
                        topic=txt.get("topic"),
                        slowmode_delay=txt.get("slowmode", 0),
                        nsfw=txt.get("nsfw", False),
                        overwrites=build_overwrites(txt.get("overwrites", []))
                    )
                    await asyncio.sleep(0.5)
                except discord.HTTPException:
                    pass

        for vc in cat_data.get("voice_channels", []):
            if not discord.utils.get(guild.voice_channels, name=vc["name"], category=category_obj):
                try:
                    await guild.create_voice_channel(
                        name=vc["name"],
                        category=category_obj,
                        user_limit=vc.get("user_limit", 0),
                        bitrate=min(vc.get("bitrate", 64000), guild.bitrate_limit),
                        overwrites=build_overwrites(vc.get("overwrites", []))
                    )
                    await asyncio.sleep(0.5)
                except discord.HTTPException:
                    pass

    await msg.edit(content="✅ **KHÔI PHỤC HOÀN TẤT!** Toàn bộ Kênh thoại, Kênh chữ, Role và Phân quyền đã được tái tạo.")

@bot.command(name="setlogg")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def setlogg(ctx, channel: discord.TextChannel = None):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}

    if channel:
        auto_delete_dict[guild_id_str]["settings"]["log_channel"] = channel.id
        save_data(auto_delete_dict)
        await ctx.send(f"✅ Đã thiết lập Kênh lưu Mod Logs thành: {channel.mention}")
    else:
        auto_delete_dict[guild_id_str]["settings"]["log_channel"] = None
        save_data(auto_delete_dict)
        await ctx.send("✅ Đã **TẮT** hệ thống xuất Mod Logs.")

@bot.command(name="lockdown")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def lockdown(ctx):
    perms = ctx.guild.default_role.permissions
    if perms.send_messages:
        perms.update(send_messages=False)
        try:
            await ctx.guild.default_role.edit(permissions=perms, reason="Khóa khẩn cấp Anti-Nuke")
            await ctx.send(f"🚨 **KHÓA SERVER KHẨN CẤP:** {ctx.author.mention} đã chặn `@everyone` gửi tin nhắn! Dùng `!lockdown` lần nữa để mở khóa.")
            await send_mod_log(ctx.guild, "🚨 Báo động: SERVER LOCKDOWN", f"**Kích hoạt bởi:** {ctx.author.mention}\n**Trạng thái:** Mọi kênh đã khóa với `@everyone`.", discord.Color.dark_red())
        except discord.Forbidden:
            await ctx.send("❌ Lỗi: Bot không đủ quyền để chỉnh sửa Role `@everyone`!")
    else:
        perms.update(send_messages=True)
        try:
            await ctx.guild.default_role.edit(permissions=perms, reason="Mở khóa Anti-Nuke")
            await ctx.send(f"✅ **MỞ KHÓA SERVER:** {ctx.author.mention} đã cho phép `@everyone` gửi tin nhắn trở lại!")
            await send_mod_log(ctx.guild, "✅ An toàn: SERVER UNLOCKED", f"**Mở khóa bởi:** {ctx.author.mention}\n**Trạng thái:** Khôi phục quyền chat cho `@everyone`.", discord.Color.green())
        except discord.Forbidden:
            await ctx.send("❌ Lỗi: Bot không đủ quyền để chỉnh sửa Role `@everyone`!")

@bot.command(name="setting")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def setting(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}

    embed = get_setting_embed(guild_id_str)
    view = SettingView(guild_id_str, ctx.author)
    await ctx.send(embed=embed, view=view)

@bot.command(name="nospam")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def nospam(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}

    current_status = auto_delete_dict[guild_id_str]["settings"].get("anti_spam", False)
    auto_delete_dict[guild_id_str]["settings"]["anti_spam"] = not current_status
    save_data(auto_delete_dict)

    limit = auto_delete_dict[guild_id_str]["settings"]["msg_limit"]
    mute_time_raw = auto_delete_dict[guild_id_str]["settings"].get("mute_time", "1p")

    if not current_status:
        await ctx.send(f"✅ Đã **BẬT 🟢** chế độ chống Spam.\n*(Nhắn quá {limit} tin trong 10 giây sẽ bị MUTE {mute_time_raw})*")
    else:
        await ctx.send("✅ Đã **TẮT 🔴** chế độ chống Spam.")

@bot.command(name="blockfakebot")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def blockfakebot(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}

    current_status = auto_delete_dict[guild_id_str]["settings"].get("block_fake_bot", False)
    auto_delete_dict[guild_id_str]["settings"]["block_fake_bot"] = not current_status
    save_data(auto_delete_dict)

    if not current_status:
        await ctx.send("✅ Đã **BẬT 🟢** chặn Bot/Webhook ma.\n*(Mọi tin nhắn từ bot/webhook không thực sự là thành viên server hoặc không có Role sẽ bị xóa ngay lập tức.)*")
    else:
        await ctx.send("✅ Đã **TẮT 🔴** chặn Bot/Webhook ma.")

@bot.command(name="autodelete")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def autodelete(ctx, *, target: str):
    clean_target = target.strip("<@!>").strip()
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False, "log_channel": None, "block_fake_bot": False}}

    if clean_target.isdigit():
        target_id = int(clean_target)
        if target_id == ctx.author.id: return await ctx.send("❌ Không thể tự bật cho chính mình!")
        if target_id == bot.user.id: return await ctx.send("❌ Không thể bật cho Bot!")
        if target_id in auto_delete_dict[guild_id_str]["ids"]: return await ctx.send(f"Đã được bật từ trước cho ID `{target_id}`.")

        member = ctx.guild.get_member(target_id)
        found_in_history = False
        if not member:
            async for msg in ctx.channel.history(limit=100):
                if msg.author.id == target_id or msg.webhook_id == target_id:
                    found_in_history = True
                    break

        if member or found_in_history:
            auto_delete_dict[guild_id_str]["ids"].append(target_id)
            save_data(auto_delete_dict)
            display = f"Bot {member.mention}" if (member and member.bot) else (f"{member.mention}" if member else f"ID `{target_id}` (Webhook/Bot ngoài)")
            await ctx.send(f"✅ Đã bật tự động xóa tin nhắn của {display} tại máy chủ này.")
        else:
            await ctx.send(f"❌ Không tìm thấy ID `{target_id}` trong server hoặc chat gần đây. **Đã hủy.**")
    else:
        target_name = target.lower()
        if target_name in [ctx.author.name.lower(), getattr(ctx.author, 'display_name', '').lower()]: return await ctx.send("❌ Không thể bật cho bản thân!")
        if target_name in [bot.user.name.lower(), getattr(bot.user, 'display_name', '').lower()]: return await ctx.send("❌ Không thể bật cho Bot!")
        if target_name in auto_delete_dict[guild_id_str]["names"]: return await ctx.send(f"Đã bật từ trước cho TÊN `{target}`.")

        member = discord.utils.find(lambda m: m.name.lower() == target_name or getattr(m, 'display_name', '').lower() == target_name, ctx.guild.members)
        found_in_history = False
        if not member:
            async for msg in ctx.channel.history(limit=100):
                if msg.author.name.lower() == target_name or getattr(msg.author, 'display_name', '').lower() == target_name:
                    found_in_history = True
                    break

        if member or found_in_history:
            auto_delete_dict[guild_id_str]["names"].append(target_name)
            save_data(auto_delete_dict)
            await ctx.send(f"✅ Đã bật tự động xóa tin nhắn đối với `{target}` tại máy chủ này.")
        else:
            await ctx.send(f"❌ Không tìm thấy `{target}` trong server hoặc chat gần đây. **Đã hủy.**")

@bot.command(name="stopdelete")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def stopdelete(ctx, *, target: str):
    guild_id_str = str(ctx.guild.id)
    server_data = auto_delete_dict.get(guild_id_str, {"ids": [], "names": []})
    clean_target = target.strip("<@!>").strip()
    removed = False

    if clean_target.isdigit():
        target_id = int(clean_target)
        if target_id in server_data["ids"]:
            server_data["ids"].remove(target_id)
            removed = True
    else:
        target_name = target.lower()
        if target_name in server_data["names"]:
            server_data["names"].remove(target_name)
            removed = True

    if removed:
        save_data(auto_delete_dict)
        await ctx.send(f"✅ Đã tắt tự động xóa tin nhắn đối với `{target}` tại máy chủ này.")
    else:
        await ctx.send(f"`{target}` hiện không nằm trong danh sách.")

@bot.command(name="listdelete")
@is_high_permission()
@commands.cooldown(1, 2, commands.BucketType.user)
async def listdelete(ctx):
    guild_id_str = str(ctx.guild.id)
    server_data = auto_delete_dict.get(guild_id_str, {"ids": [], "names": []})
    ids = server_data.get("ids", [])
    names = server_data.get("names", [])

    if not ids and not names:
        return await ctx.send("Hiện tại không có ai trong danh sách tự động xóa.")

    items = []
    targets_for_buttons = []

    for user_id in ids:
        member = ctx.guild.get_member(user_id)
        if member:
            items.append(f"• {member.mention} (ID: `{user_id}`)" if member.bot else f"• {member.mention}")
            targets_for_buttons.append(("id", user_id, member.display_name))
        else:
            items.append(f"• ID: `{user_id}` (Ngoài server/Webhook)")
            targets_for_buttons.append(("id", user_id, str(user_id)))

    for name in names:
        items.append(f"• Tên/Webhook: `{name}`")
        targets_for_buttons.append(("name", name, name))
    
    list_str = "\n".join(items)

    class StopDeleteView(discord.ui.View):
        def __init__(self, original_author):
            super().__init__(timeout=300)
            self.original_author = original_author
            for t_type, val, label in targets_for_buttons[:25]:
                btn = discord.ui.Button(style=discord.ButtonStyle.danger, label=f"❌ Tắt {label}"[:80])
                btn.callback = self.create_callback(btn, t_type, val)
                self.add_item(btn)

        def create_callback(self, button, t_type, val):
            async def callback(interaction: discord.Interaction):
                if interaction.user != self.original_author:
                    return await interaction.response.send_message(f"❌ Danh sách này được gọi bởi {self.original_author.mention}.", ephemeral=True)
                
                p = interaction.user.guild_permissions
                if not (p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild):
                    return await interaction.response.send_message("❌ Thiếu quyền!", ephemeral=True)

                current_server_data = auto_delete_dict.get(str(interaction.guild.id), {"ids": [], "names": []})
                removed = False
                if t_type == "id" and val in current_server_data["ids"]:
                    current_server_data["ids"].remove(val)
                    removed = True
                elif t_type == "name" and val in current_server_data["names"]:
                    current_server_data["names"].remove(val)
                    removed = True
                
                if removed:
                    save_data(auto_delete_dict)
                    await interaction.response.send_message(f"✅ Đã tắt tự động xóa cho `{val}`.", ephemeral=False)
                    button.disabled = True
                    button.style = discord.ButtonStyle.secondary
                    await interaction.message.edit(view=self)
                else:
                    await interaction.response.send_message("⚠️ Đã tắt từ trước!", ephemeral=True)
            return callback

    await ctx.send(f"📋 **Danh sách bị tự động xóa:**\n{list_str}\n*(Bấm nút để tắt nhanh)*", view=StopDeleteView(ctx.author))

# --- HỆ THỐNG BẮT LỖI TỔNG (GLOBAL ERROR HANDLER) GIÚP CHỐNG SPAM LỆNH ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        # Bắt được ai đó (kể cả Admin) spam quá nhanh
        msg = await ctx.send(f"⏳ **Chậm lại!** Vui lòng đợi `{error.retry_after:.1f} giây` để dùng tiếp lệnh này.", delete_after=3)
    elif isinstance(error, commands.NotOwner):
        await ctx.author.send("❌ Chỉ Chủ sở hữu tài khoản Bot mới có quyền dùng lệnh này!") if ctx.guild is None else await ctx.send(f"❌ {ctx.author.mention}, chỉ Chủ sở hữu tài khoản Bot mới có quyền dùng lệnh này!", delete_after=10)
    elif isinstance(error, commands.CheckFailure):
        await ctx.send(f"❌ {ctx.author.mention}, m có quyền dell đâu mà sài lol", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Lỗi cú pháp! Gõ thiếu thông tin.", delete_after=10)
    else:
        # Bỏ qua các lỗi lặt vặt khác để không spam log console
        pass

keep_alive()
token = os.environ.get("BOT_TOKEN")
if token: bot.run(token)
else: print("LỖI: Chưa cài BOT_TOKEN trên Render!")
