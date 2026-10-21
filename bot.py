import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os
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

# Lưu trữ độc lập theo Server: { guild_id: {"ids": [...], "names": [...]} }
auto_delete_dict = {}
tag_spam_tracker = {}

# HÀM KIỂM TRA QUYỀN CAO (STAFF / MOD / ADMIN)
def is_high_permission():
    async def predicate(ctx):
        p = ctx.author.guild_permissions
        if p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild:
            return True
        raise commands.CheckFailure("Lỗi thiếu quyền")
    return commands.check(predicate)

@bot.event
async def on_ready():
    print(f'Bot {bot.user} đã Online!')

@bot.event
async def on_message(message):
    if message.author == bot.user or not message.guild:
        return

    guild_id = message.guild.id
    server_data = auto_delete_dict.get(guild_id, {"ids": [], "names": []})
    blacklisted_ids = server_data["ids"]
    blacklisted_names = server_data["names"]

    # KIỂM TRA XEM TIN NHẮN CÓ THUỘC DANH SÁCH BỊ XÓA KHÔNG
    is_blacklisted = False
    author_id = message.author.id
    author_name = message.author.name.lower()
    display_name = getattr(message.author, 'display_name', '').lower()

    if author_id in blacklisted_ids or (message.webhook_id and message.webhook_id in blacklisted_ids):
        is_blacklisted = True
    elif author_name in blacklisted_names or display_name in blacklisted_names:
        is_blacklisted = True

    # NẾU THUỘC DANH SÁCH ĐEN -> XỬ LÝ GHOST PING & XÓA TIN NHẮN
    if is_blacklisted:
        valid_mentions = [user for user in message.mentions if user != message.author]
        
        if valid_mentions:
            tracker_key = (guild_id, author_id)
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

            if current_count >= 5:
                if isinstance(message.author, discord.Member):
                    try:
                        await message.author.timeout(timedelta(minutes=1), reason="Spam tag quá 5 lần trong danh sách đen")
                        await message.channel.send(
                            f"🚫 {message.author.mention} đã bị **MUTE 1 phút** do cố tình spam tag quá 5 lần!",
                            delete_after=15
                        )
                    except discord.Forbidden:
                        pass
                tag_spam_tracker[tracker_key] = {"count": 0, "last_time": now}
            else:
                pinged_users = ", ".join([user.mention for user in valid_mentions])
                sender_display = message.author.mention if hasattr(message.author, 'mention') else f"**{message.author.name}**"
                await message.channel.send(
                    f"⚠️ **Ghost Ping!** {sender_display} vừa tag {pinged_users} ({current_count}/5 lần - Tag 5 lần sẽ bị MUTE 1 phút).",
                    delete_after=10
                )

        try:
            await message.delete()
        except discord.Forbidden:
            pass
            
    await bot.process_commands(message)

# --- 3. LỆNH ĐIỀU KHIỂN ---

@bot.command(name="autodelete")
@is_high_permission()
async def autodelete(ctx, *, target: str):
    clean_target = target.strip("<@!>").strip()

    if clean_target.isdigit():
        target_id = int(clean_target)
        member = ctx.guild.get_member(target_id)
        
        if target_id == ctx.author.id:
            return await ctx.send("❌ Bạn không thể tự bật tính năng tự động xóa tin nhắn của chính mình!")
        if target_id == bot.user.id:
            return await ctx.send("❌ Không thể bật tính năng tự động xóa tin nhắn cho Bot!")

        display_str = f"Bot {member.mention} (ID: `{target_id}`)" if (member and member.bot) else (f"{member.mention}" if member else f"ID `{target_id}`")

        guild_id = ctx.guild.id
        if guild_id not in auto_delete_dict:
            auto_delete_dict[guild_id] = {"ids": [], "names": []}

        if target_id not in auto_delete_dict[guild_id]["ids"]:
            auto_delete_dict[guild_id]["ids"].append(target_id)
            await ctx.send(f"Đã bật tự động xóa tin nhắn của {display_str} tại máy chủ này.")
        else:
            await ctx.send(f"Bạn đã dùng lệnh này rồi và hiện tại đang tự động xóa tin nhắn của {display_str}.")
            
    else:
        target_name = target.lower()
        if target_name in [ctx.author.name.lower(), ctx.author.display_name.lower()]:
            return await ctx.send("❌ Bạn không thể tự bật tính năng tự động xóa tin nhắn của chính mình!")
        if target_name in [bot.user.name.lower(), bot.user.display_name.lower()]:
            return await ctx.send("❌ Không thể bật tính năng tự động xóa tin nhắn cho Bot!")

        guild_id = ctx.guild.id
        if guild_id not in auto_delete_dict:
            auto_delete_dict[guild_id] = {"ids": [], "names": []}

        if target_name not in auto_delete_dict[guild_id]["names"]:
            auto_delete_dict[guild_id]["names"].append(target_name)
            await ctx.send(f"Đã bật tự động xóa tin nhắn đối với TÊN/WEBHOOK `{target}` tại máy chủ này.")
        else:
            await ctx.send(f"Bạn đã dùng lệnh này rồi và hiện tại đang tự động xóa tin nhắn của TÊN/WEBHOOK `{target}`.")

@bot.command(name="stopdelete")
@is_high_permission()
async def stopdelete(ctx, *, target: str):
    guild_id = ctx.guild.id
    server_data = auto_delete_dict.get(guild_id, {"ids": [], "names": []})
    clean_target = target.strip("<@!>").strip()

    removed = False
    if clean_target.isdigit():
        target_id = int(clean_target)
        member = ctx.guild.get_member(target_id)
        display_str = f"Bot {member.mention} (ID: `{target_id}`)" if (member and member.bot) else (f"{member.mention}" if member else f"ID `{target_id}`")

        if target_id in server_data["ids"]:
            server_data["ids"].remove(target_id)
            removed = True
            await ctx.send(f"Đã tắt tự động xóa tin nhắn đối với {display_str} tại máy chủ này.")
        else:
            await ctx.send(f"{display_str} hiện không nằm trong danh sách tự động xóa của máy chủ này.")
    else:
        target_name = target.lower()
        if target_name in server_data["names"]:
            server_data["names"].remove(target_name)
            removed = True
            await ctx.send(f"Đã tắt tự động xóa tin nhắn đối với TÊN/WEBHOOK `{target}` tại máy chủ này.")
        else:
            await ctx.send(f"`{target}` hiện không nằm trong danh sách tự động xóa của máy chủ này.")

# --- LỆNH LIST KÈM NÚT BẤM (BUTTONS) ---
@bot.command(name="listdelete")
@is_high_permission()
async def listdelete(ctx):
    guild_id = ctx.guild.id
    server_data = auto_delete_dict.get(guild_id, {"ids": [], "names": []})

    ids = server_data.get("ids", [])
    names = server_data.get("names", [])

    if not ids and not names:
        return await ctx.send("Hiện tại máy chủ này không có ai trong danh sách tự động xóa tin nhắn.")

    items = []
    targets_for_buttons = []

    for user_id in ids:
        member = ctx.guild.get_member(user_id)
        if member:
            items.append(f"• {member.mention} (ID: `{user_id}`)" if member.bot else f"• {member.mention}")
            targets_for_buttons.append(("id", user_id, member.display_name))
        else:
            items.append(f"• ID: `{user_id}`")
            targets_for_buttons.append(("id", user_id, str(user_id)))
            
    for name in names:
        items.append(f"• Tên/Webhook: `{name}`")
        targets_for_buttons.append(("name", name, name))
    
    list_str = "\n".join(items)

    # Class tạo Nút bấm tương tác
    class StopDeleteView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300) # Nút bấm có hiệu lực trong 5 phút
            for t_type, val, label in targets_for_buttons[:25]: # Discord giới hạn tối đa 25 nút 1 tin nhắn
                btn = discord.ui.Button(style=discord.ButtonStyle.danger, label=f"❌ Tắt {label}"[:80])
                btn.callback = self.create_callback(btn, t_type, val)
                self.add_item(btn)

        def create_callback(self, button, t_type, val):
            async def callback(interaction: discord.Interaction):
                # Kiểm tra quyền người ấn nút
                p = interaction.user.guild_permissions
                if not (p.administrator or p.manage_messages or p.kick_members or p.ban_members or p.manage_guild):
                    return await interaction.response.send_message("❌ Mày đâu có quyền Quản trị / Staff mà bấm nút này!", ephemeral=True)

                current_server_data = auto_delete_dict.get(interaction.guild.id, {"ids": [], "names": []})
                
                removed = False
                if t_type == "id" and val in current_server_data["ids"]:
                    current_server_data["ids"].remove(val)
                    removed = True
                elif t_type == "name" and val in current_server_data["names"]:
                    current_server_data["names"].remove(val)
                    removed = True
                
                if removed:
                    await interaction.response.send_message(f"✅ Đã tắt tự động xóa tin nhắn đối với `{val}`.", ephemeral=False)
                    # Sau khi tắt thành công, làm xám nút bấm đi
                    button.disabled = True
                    button.style = discord.ButtonStyle.secondary
                    await interaction.message.edit(view=self)
                else:
                    await interaction.response.send_message("⚠️ Mục này đã được tắt từ trước rồi!", ephemeral=True)
            return callback

    view = StopDeleteView()
    msg = f"📋 **Danh sách bị tự động xóa tại máy chủ này:**\n{list_str}\n\n*(Bấm vào các nút màu đỏ bên dưới để ngừng xóa ngay lập tức)*"
    await ctx.send(msg, view=view)

# BÁO LỖI NẾU KHÔNG CÓ QUYỀN HOẶC GÕ THIẾU TÊN
@autodelete.error
@stopdelete.error
async def command_permission_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(
            f"❌ {ctx.author.mention}, m đâu có quyền Quản trị / Staff trong server này dell đâu",
            delete_after=10
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"⚠️ Vui lòng nhập Tên, ID hoặc Tag người dùng! Cú pháp: `{ctx.prefix}{ctx.command.name} [Tên / ID / Tag]`",
            delete_after=10
        )

# --- 4. KHỞI CHẠY BOT ---
keep_alive()

token = os.environ.get("BOT_TOKEN")
if token:
    bot.run(token)
else:
    print("LỖI: Chưa cài BOT_TOKEN trên Render!")
