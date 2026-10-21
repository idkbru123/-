import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os

# --- 1. WEB SERVER ẢO GIỮ BOT ONLINE TRÊN RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Web server đang chạy để giữ bot online!"

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

@bot.event
async def on_ready():
    print(f'Bot {bot.user} đã Online và sẵn sàng hoạt động!')

@bot.event
async def on_message(message):
    # Bỏ qua tin nhắn của chính bot
    if message.author == bot.user:
        return

    # Tự động xóa tin nhắn nếu người gửi nằm trong danh sách
    if message.author.id in auto_delete_list:
        try:
            await message.delete()
        except discord.Forbidden:
            pass
            
    await bot.process_commands(message)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def autodelete(ctx, member: discord.Member):
    if member.id not in auto_delete_list:
        auto_delete_list.append(member.id)
        await ctx.send(f"Đã bật chế độ TỰ ĐỘNG XÓA tin nhắn của {member.mention}.")
    else:
        await ctx.send(f"{member.mention} đã nằm trong danh sách đen từ trước.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def stopdelete(ctx, member: discord.Member):
    if member.id in auto_delete_list:
        auto_delete_list.remove(member.id)
        await ctx.send(f"Đã tắt chế độ tự động xóa tin nhắn đối với {member.mention}.")
    else:
        await ctx.send(f"{member.mention} không nằm trong danh sách đen.")

# --- 3. KHỞI CHẠY BOT ---
keep_alive()

# Lấy Token an toàn từ biến môi trường "BOT_TOKEN" đã cài trên Render
token = os.environ.get("BOT_TOKEN")
if token:
    bot.run(token)
else:
    print("LỖI: Chưa thiết lập biến môi trường BOT_TOKEN trên Render!")
