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

# Lưu trữ độc lập theo từng Server: { guild_id: [user_id1, user_id2] }
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
    if message.author == bot.user or message.author.bot or not message.guild:
        return

    guild_id = message.guild.id
    server_blacklist = auto_delete_dict.get(guild_id, [])

    # Tự động xóa & chống Ghost Ping chỉ áp dụng TRONG SERVER NÀY
    if message.author.id in server_blacklist:
        valid_mentions = [user for user in message.mentions if user != message.author]
        
        if valid_mentions:
            tracker_key = (guild_id, message.author.id)
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

            # MUTE 1 PHÚT NẾU SPAM TAG QUÁ 5 LẦN
            if current_count >= 5:
                try:
                    await message.author.timeout(timedelta(minutes=1), reason="Spam tag quá 5 lần trong danh sách đen")
                    await message.channel.send(
                        f"🚫 {message.author.mention} đã bị **MUTE 1 phút** do cố tình spam tag quá 5 lần!",
                        delete_after=15
                    )
                except discord.Forbidden:
                    await message.channel.send(
                        f"⚠️ Bot không thể Mute {message.author.mention} (Thiếu quyền 'Moderate Members' hoặc Role của bot thấp hơn người này).",
                        delete_after=15
                    )
                tag_spam_tracker[tracker_key] = {"count": 0, "last_time": now}
            else:
                pinged_users = ", ".join([user.mention for user in valid_mentions])
                await message.channel.send(
                    f"⚠️ **Ghost Ping!** {message.author.mention} vừa tag {pinged_users} ({current_count}/5 lần - Tag 5 lần sẽ bị MUTE 1 phút).",
                    delete_after=10
                )

        # Xóa tin nhắn
        try:
            await message.delete()
        except discord.Forbidden:
            pass
            
    await bot.process_commands(message)

# --- 3. LỆNH ĐIỀU KHIỂN (RIÊNG CHO TỪNG SERVER) ---

@bot.command(name="autodelete")
@is_high_permission()
async def autodelete(ctx, member: discord.Member):
    guild_id = ctx.guild.id
    if guild_id not in auto_delete_dict:
        auto_delete_dict[guild_id] = []

    if member.id not in auto_delete_dict[guild_id]:
        auto_delete_dict[guild_id].append(member.id)
        await ctx.send(f"Đã bật TỰ ĐỘNG XÓA tin nhắn của {member.mention} tại máy chủ này.")
    else:
        await ctx.send(f"Bạn đã dùng lệnh này rồi và hiện tại đang tự động xóa tin nhắn của {member.mention} tại máy chủ này.")

@bot.command(name="stopdelete")
@is_high_permission()
async def stopdelete(ctx, member: discord.Member):
    guild_id = ctx.guild.id
    server_blacklist = auto_delete_dict.get(guild_id, [])

    if member.id in server_blacklist:
        server_blacklist.remove(member.id)
        tracker_key = (guild_id, member.id)
        if tracker_key in tag_spam_tracker:
            del tag_spam_tracker[tracker_key]
        await ctx.send(f"Đã tắt tự động xóa tin nhắn đối với {member.mention} tại máy chủ này.")
    else:
        await ctx.send(f"{member.mention} hiện không nằm trong danh sách tự động xóa của máy chủ này.")

@bot.command(name="listdelete")
@is_high_permission()
async def listdelete(ctx):
    guild_id = ctx.guild.id
    server_blacklist = auto_delete_dict.get(guild_id, [])

    if not server_blacklist:
        await ctx.send("Hiện tại máy chủ này không có ai trong danh sách tự động xóa tin nhắn.")
    else:
        members_mentions = [f"<@{user_id}>" for user_id in server_blacklist]
        list_str = ", ".join(members_mentions)
        await ctx.send(f"📋 **Danh sách bị tự động xóa tại máy chủ này:** {list_str}")

# BÁO LỖI NẾU KHÔNG CÓ QUYỀN HOẶC GÕ THIẾU TÊN
@autodelete.error
@stopdelete.error
async def command_permission_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(
            f"❌ {ctx.author.mention}, bạn cần có quyền Quản trị / Staff trong server mới dùng được lệnh này!",
            delete_after=10
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            f"⚠️ Vui lòng tag tên người dùng! Cú pháp: `{ctx.prefix}{ctx.command.name} @nguoidung`",
            delete_after=10
        )

# --- 4. KHỞI CHẠY BOT ---
keep_alive()

token = os.environ.get("BOT_TOKEN")
if token:
    bot.run(token)
else:
    print("LỖI: Chưa cài BOT_TOKEN trên Render!")
