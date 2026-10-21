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

# Bộ đếm spam tag: {user_id: {"count": số_lần, "last_time": thời_gian_gần_nhất}}
tag_spam_tracker = {}

@bot.event
async def on_ready():
    print(f'Bot {bot.user} đã Online!')

@bot.event
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    # Kiểm tra nếu người gửi nằm trong danh sách đen
    if message.author.id in auto_delete_list:
        # Lọc danh sách tag hợp lệ (không tính tự tag chính mình)
        valid_mentions = [user for user in message.mentions if user != message.author]
        
        if valid_mentions:
            user_id = message.author.id
            now = datetime.now()
            
            # Khởi tạo hoặc cập nhật bộ đếm spam
            if user_id not in tag_spam_tracker:
                tag_spam_tracker[user_id] = {"count": 1, "last_time": now}
            else:
                # Nếu lần tag trước cách xa quá 60 giây -> Reset đếm lại từ 1
                if (now - tag_spam_tracker[user_id]["last_time"]).total_seconds() > 60:
                    tag_spam_tracker[user_id] = {"count": 1, "last_time": now}
                else:
                    tag_spam_tracker[user_id]["count"] += 1
                    tag_spam_tracker[user_id]["last_time"] = now

            current_count = tag_spam_tracker[user_id]["count"]

            # XỬ LÝ KHI SPAM TAG QUÁ 5 LẦN -> MUTE 1 PHÚT
            if current_count >= 5:
                try:
                    # Timeout (Mute) người dùng trong 1 phút
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
                # Reset bộ đếm về 0 sau khi thi hành phạt
                tag_spam_tracker[user_id] = {"count": 0, "last_time": now}
            else:
                # Cảnh báo Ghost Ping bình thường + hiển thị số lần cảnh báo
                pinged_users = ", ".join([user.mention for user in valid_mentions])
                await message.channel.send(
                    f"⚠️ **Ghost Ping!** {message.author.mention} vừa tag {pinged_users} ({current_count}/5 lần - Tag 5 lần sẽ bị MUTE 1 phút).",
                    delete_after=10
                )

        # Xóa tin nhắn vi phạm
        try:
            await message.delete()
        except discord.Forbidden:
            pass
            
    await bot.process_commands(message)

# --- 3. LỆNH ĐIỀU KHIỂN ---
@bot.command(name="autodelete")
@commands.has_permissions(manage_messages=True)
async def autodelete(ctx, member: discord.Member):
    if member.id not in auto_delete_list:
        auto_delete_list.append(member.id)
        await ctx.send(f"Đã bật TỰ ĐỘNG XÓA tin nhắn của {member.mention}.")
    else:
        await ctx.send(f"{member.mention} đã nằm trong danh sách đen từ trước.")

@bot.command(name="stopdelete")
@commands.has_permissions(manage_messages=True)
async def stopdelete(ctx, member: discord.Member):
    if member.id in auto_delete_list:
        auto_delete_list.remove(member.id)
        if member.id in tag_spam_tracker:
            del tag_spam_tracker[member.id]
        await ctx.send(f"Đã tắt tự động xóa tin nhắn đối với {member.mention}.")
    else:
        await ctx.send(f"{member.mention} không nằm trong danh sách đen.")

# --- 4. KHỞI CHẠY BOT ---
keep_alive()

token = os.environ.get("BOT_TOKEN")
if token:
    bot.run(token)
else:
    print("LỖI: Chưa cài BOT_TOKEN trên Render!")
