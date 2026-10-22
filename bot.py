import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os
import json
import re
import requests
from datetime import datetime, timedelta

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

bot = commands.Bot(command_prefix="!", intents=intents)

# --- HỆ THỐNG LƯU TRỮ VĨNH VIỄN QUA UPSTASH REDIS (THAY CHO FILE JSON) ---
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
REDIS_KEY = "bot_data"

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
            return json.loads(result)
    except Exception as e:
        print(f"Lỗi khi load data từ Upstash: {e}")
    return {}

def save_data(data):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return
    try:
        requests.post(
            f"{UPSTASH_URL}/set/{REDIS_KEY}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
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

# HÀM KIỂM TRA QUYỀN CAO
def is_high_permission():
    async def predicate(ctx):
        p = ctx.author.guild_permissions
        if p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild:
            return True
        raise commands.CheckFailure("Lỗi thiếu quyền")
    return commands.check(predicate)

# HÀM QUY ĐỔI CHUỖI THỜI GIAN THÀNH GIÂY (THÔNG MINH)
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

# --- HÀM TẠO GIAO DIỆN EMBED CHO SETTING ---
def get_setting_embed(guild_id_str):
    settings = auto_delete_dict[guild_id_str]["settings"]
    status = "🟢 ĐANG BẬT" if settings.get("anti_spam", False) else "🔴 ĐANG TẮT"
    
    mute_display = settings.get('mute_time', '1p')
    if isinstance(mute_display, int): mute_display = f"{mute_display} phút"
    
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
        value=f"• Trạng thái tính năng: **{status}**\n• Mốc 1 (Cảnh cáo): **{settings.get('warn_limit', 5)} tin nhắn** / 10s\n• Mốc 2 (Bị Mute): **{settings['msg_limit']} tin nhắn** / 10s\n• Chống copy-paste: **Xóa tin nhắn từ lần trùng thứ 5 (Staff/Bot chỉ xóa, không mute)**", 
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
                return await interaction.response.send_message("❌ Lỗi: Mốc CẢNH CÁO phải NHỎ HƠN Mốc PHẠT (Mute)!", ephemeral=True)
            if self.setting_key == "msg_limit" and new_val <= current_settings.get("warn_limit", 5):
                return await interaction.response.send_message("❌ Lỗi: Mốc PHẠT (Mute) phải LỚN HƠN Mốc CẢNH CÁO!", ephemeral=True)

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

    @discord.ui.button(label="Mốc Tag", style=discord.ButtonStyle.primary, emoji="🏷️")
    async def edit_tag(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("tag_limit", "Giới hạn Spam Tag (Lần)", "Ví dụ: 5", self))

    @discord.ui.button(label="Mốc Cảnh Cáo", style=discord.ButtonStyle.primary, emoji="⚠️")
    async def edit_warn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("warn_limit", "Mốc Nhắc Nhở Spam (trong 10s)", "Ví dụ: 5", self))

    @discord.ui.button(label="Mốc Phạt (Mute)", style=discord.ButtonStyle.danger, emoji="💬")
    async def edit_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SettingModal("msg_limit", "Mốc Phạt Mute/Nuke (trong 10s)", "Ví dụ: 10", self))

@bot.event
async def on_ready():
    print(f'Bot {bot.user} đã Online và tải xong dữ liệu!')

@bot.event
async def on_message(message):
    if message.author == bot.user or not message.guild:
        return

    guild_id_str = str(message.guild.id)
    
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False}}
    elif "settings" not in auto_delete_dict[guild_id_str]:
        auto_delete_dict[guild_id_str]["settings"] = {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False}
    else:
        if "warn_limit" not in auto_delete_dict[guild_id_str]["settings"]:
            auto_delete_dict[guild_id_str]["settings"]["warn_limit"] = 5
        if "msg_limit" not in auto_delete_dict[guild_id_str]["settings"] or auto_delete_dict[guild_id_str]["settings"]["msg_limit"] <= auto_delete_dict[guild_id_str]["settings"]["warn_limit"]:
            auto_delete_dict[guild_id_str]["settings"]["msg_limit"] = auto_delete_dict[guild_id_str]["settings"]["warn_limit"] + 5

    server_data = auto_delete_dict[guild_id_str]
    server_settings = server_data["settings"]
    author_id = message.author.id
    
    mute_time_raw = server_settings.get("mute_time", "1p")
    mute_seconds = parse_time_to_seconds(mute_time_raw)
    if mute_seconds <= 0: mute_seconds = 60

    # Kiểm tra xem người nhắn có phải là Staff / Admin / Bot hoặc Webhook trong server không
    is_staff_or_bot = message.author.bot
    if isinstance(message.author, discord.Member):
        p = message.author.guild_permissions
        if p.administrator or p.manage_messages or p.manage_guild or p.kick_members or p.ban_members:
            is_staff_or_bot = True

    # 1. HỆ THỐNG CHỐNG SPAM TOÀN DIỆN
    if server_settings.get("anti_spam", False):
        tracker_key = (guild_id_str, author_id)
        now = datetime.now()
        
        # --- 1A. KIỂM TRA SPAM NHANH (FAST SPAM) ---
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

        elif current_msg_count >= msg_limit:
            # Nếu là Staff hoặc Bot: chỉ xóa tin nhắn mới nhất, KHÔNG MUTE
            if is_staff_or_bot:
                try:
                    await message.delete()
                except discord.Forbidden:
                    pass
            else:
                # Nếu là thành viên thường: Tiến hành Mute
                if isinstance(message.author, discord.Member) and not message.webhook_id:
                    try:
                        await message.author.timeout(timedelta(seconds=mute_seconds), reason="Phớt lờ cảnh cáo Spam")
                        await message.channel.send(f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do tiếp tục spam sau khi bị cảnh cáo!", delete_after=15)
                    except discord.Forbidden:
                        pass
                else:
                    target_name = message.author.name.lower()
                    if target_name not in auto_delete_dict[guild_id_str]["names"]:
                        auto_delete_dict[guild_id_str]["names"].append(target_name)
                        save_data(auto_delete_dict)
                        try:
                            await message.channel.purge(limit=msg_limit + 5, check=lambda m: m.author.id == author_id or (m.webhook_id and m.webhook_id == message.webhook_id))
                        except discord.Forbidden:
                            pass
                        await message.channel.send(f"🛡️ **Anti-Nuke:** Phát hiện Webhook lạ (`{message.author.name}`) đang spam phá hoại! Đã khóa vào Danh Sách Đen.", delete_after=15)
            msg_spam_tracker[tracker_key] = {"count": 0, "start_time": now}


        # --- 1B. KIỂM TRA SPAM TỪ TỪ VÀ TRÙNG LẶP NỘI DUNG ---
        msg_content = message.content.strip().lower()
        if msg_content: 
            if tracker_key in slow_spam_tracker:
                tracker = slow_spam_tracker[tracker_key]
                if tracker["content"] == msg_content and (now - tracker["last_time"]).total_seconds() < 60:
                    tracker["count"] += 1
                    
                    if tracker["count"] >= 5:
                        try:
                            await message.delete() 
                        except discord.Forbidden:
                            pass
                        
                        # Nếu là Staff/Bot thì chỉ xóa tin nhắn lặp chứ tuyệt đối không mute
                        if not is_staff_or_bot:
                            if tracker["first_duplicate_time"] is None:
                                tracker["first_duplicate_time"] = now 
                            else:
                                if (now - tracker["first_duplicate_time"]).total_seconds() >= 20:
                                    if isinstance(message.author, discord.Member) and not message.webhook_id:
                                        try:
                                            await message.author.timeout(timedelta(seconds=mute_seconds), reason="Spam lặp một nội dung quá lâu")
                                            await message.channel.send(f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do cố tình spam lặp đi lặp lại cùng một nội dung liên tục!", delete_after=15)
                                        except discord.Forbidden:
                                            pass
                                    else:
                                        target_name = message.author.name.lower()
                                        if target_name not in auto_delete_dict[guild_id_str]["names"]:
                                            auto_delete_dict[guild_id_str]["names"].append(target_name)
                                            save_data(auto_delete_dict)
                                            await message.channel.send(f"🛡️ **Anti-Nuke:** Khóa Webhook lạ (`{message.author.name}`) do spam lặp chữ!", delete_after=15)
                                    
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
                if isinstance(message.author, discord.Member) and not is_staff_or_bot:
                    try:
                        await message.author.timeout(timedelta(seconds=mute_seconds), reason="Spam tag")
                        await message.channel.send(
                            f"🚫 {message.author.mention} đã bị **MUTE {mute_time_raw}** do cố tình spam tag quá {tag_limit} lần!",
                            delete_after=15
                        )
                    except discord.Forbidden:
                        pass
                tag_spam_tracker[tracker_key] = {"count": 0, "last_time": now}
            else:
                pinged_users = ", ".join([user.mention for user in valid_mentions])
                sender_display = message.author.mention if hasattr(message.author, 'mention') else f"**{message.author.name}**"
                await message.channel.send(
                    f"⚠️ **Ghost Ping!** {sender_display} vừa tag {pinged_users} ({current_count}/{tag_limit} lần).",
                    delete_after=10
                )
        try:
            await message.delete()
        except discord.Forbidden:
            pass
            
    await bot.process_commands(message)

# --- 3. LỆNH ĐIỀU KHIỂN ---

@bot.command(name="setting")
@is_high_permission()
async def setting(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False}}
    
    embed = get_setting_embed(guild_id_str)
    view = SettingView(guild_id_str, ctx.author)
    await ctx.send(embed=embed, view=view)

@bot.command(name="nospam")
@is_high_permission()
async def nospam(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False}}
    
    current_status = auto_delete_dict[guild_id_str]["settings"].get("anti_spam", False)
    auto_delete_dict[guild_id_str]["settings"]["anti_spam"] = not current_status
    save_data(auto_delete_dict)
    
    limit = auto_delete_dict[guild_id_str]["settings"]["msg_limit"]
    mute_time_raw = auto_delete_dict[guild_id_str]["settings"].get("mute_time", "1p")
    
    if not current_status:
        await ctx.send(f"✅ Đã **BẬT 🟢** chế độ chống Spam.\n*(Nhắn quá {limit} tin trong 10 giây sẽ bị MUTE {mute_time_raw})*")
    else:
        await ctx.send("✅ Đã **TẮT 🔴** chế độ chống Spam.")

@bot.command(name="autodelete")
@is_high_permission()
async def autodelete(ctx, *, target: str):
    clean_target = target.strip("<@!>").strip()
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in auto_delete_dict:
        auto_delete_dict[guild_id_str] = {"ids": [], "names": [], "settings": {"tag_limit": 5, "warn_limit": 5, "msg_limit": 10, "mute_time": "1p", "anti_spam": False}}

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

@autodelete.error
@stopdelete.error
@setting.error
@nospam.error
async def command_permission_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(f"❌ {ctx.author.mention}, bạn cần quyền Quản trị / Staff!", delete_after=10)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Lỗi cú pháp!", delete_after=10)

keep_alive()
token = os.environ.get("BOT_TOKEN")
if token: bot.run(token)
else: print("LỖI: Chưa cài BOT_TOKEN trên Render!")
