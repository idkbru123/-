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
auto_delete_list = []
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
    if message.author == bot.user or message.author.bot:
        return

    # Tự động xóa & chống Ghost Ping / Spam Tag đối với người trong danh sách đen
    if message.author.id in auto_delete_list:
        valid_mentions = [user for user in message.mentions if user != message.author]
        
        if valid_mentions:
            user_id = message.author.id
            now = datetime.now()
            
            if user_id not in tag_spam_tracker:
                tag_spam_tracker[user_id] = {"count": 1, "last_time": now}
            else:
                if (now - tag_spam_tracker[user_id]["last_time"]).total_seconds() > 60:
                    tag_spam_tracker[user_id] = {"count": 1, "last_time": now}
                else:
                    tag_spam_tracker[user_id]["count"] += 1
                    tag_spam_tracker[user_id]["last_time"] = now

            current_count = tag_spam_tracker[user_id]["count"]

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
                tag_spam_tracker[user_id] = {"count": 0, "last_time": now}
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

# --- 3. LỆNH ĐIỀU KHIỂN (YÊU CẦU QUYỀN CAO) ---

@bot.command(name="autodelete")
@is_high_permission()
async def autodelete(ctx, member: discord.Member):
    if member.id not in auto_delete_list:
        auto_delete_list.append(member.id)
        await ctx.send(f"Đã bật TỰ ĐỘNG XÓA tin nhắn của {member.mention}.")
    else:
        # Thông báo nhắc nhở khi đã bật từ trước
        await ctx.send(f"Bạn đã dùng lệnh này rồi và hiện tại đang tự động xóa tin nhắn của {member.mention}.")

@bot.command(name="stopdelete")
@is_high_permission()
async def stopdelete(ctx, member: discord.Member):
    if member.id in auto_delete_list:
        auto_delete_list.remove(member.id)
        if member.id in tag_spam_tracker:
            del tag_spam_tracker[member.id]
        await ctx.send(f"Đã tắt tự động xóa tin nhắn đối với {member.mention}.")
    else:
        await ctx.send(f"{member.mention} hiện không nằm trong danh sách tự động xóa.")

# LỆNH XEM DANH SÁCH NHỮNG NGƯỜI ĐANG BỊ TỰ ĐỘNG XÓA TIN NHẮN
@bot.command(name="listdelete")
@is_high_permission()
async def listdelete(ctx):
    if not auto_delete_list:
        await ctx.send("Hiện tại không có ai trong danh sách tự động xóa tin nhắn.")
    else:
        members_mentions = [f"<@{user_id}>" for user_id in auto_delete_list]
        list_str = ", ".join(members_mentions)
        await ctx.send(f"📋 **Danh sách đang bị tự động xóa tin nhắn:** {list_str}")

# BÁO LỖI NẾU NGƯỜI DÙNG KHÔNG CÓ QUYỀN HOẶC GÕ THIẾU TÊN
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
