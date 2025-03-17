import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import youtube_dl

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Настройки youtube_dl
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn',
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # берем первый элемент из плейлиста
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Создание экземпляра бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user.name} подключился к Discord!')
    print(f'ID бота: {bot.user.id}')
    print('------')

@bot.command(name='play', help='Проигрывание музыки из YouTube')
async def play(ctx, *, url):
    if not ctx.message.author.voice:
        await ctx.send('Вы должны быть в голосовом канале, чтобы использовать эту команду!')
        return

    channel = ctx.message.author.voice.channel
    
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)
    
    async with ctx.typing():
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ctx.voice_client.play(player, after=lambda e: print(f'Ошибка проигрывания: {e}') if e else None)
    
    await ctx.send(f'Сейчас играет: {player.title}')

@bot.command(name='pause', help='Приостановить проигрывание')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send('Музыка приостановлена.')
    else:
        await ctx.send('Нет проигрываемой музыки для паузы.')

@bot.command(name='resume', help='Возобновить проигрывание')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send('Воспроизведение возобновлено.')
    else:
        await ctx.send('Музыка не на паузе.')

@bot.command(name='stop', help='Остановить проигрывание и выйти из канала')
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send('Музыка остановлена и бот отключен от канала.')
    else:
        await ctx.send('Бот не подключен к голосовому каналу.')

@bot.command(name='volume', help='Регулировать громкость (0-100)')
async def volume(ctx, volume: int):
    if ctx.voice_client is None:
        return await ctx.send('Бот не подключен к голосовому каналу.')
    
    volume = max(0, min(100, volume))
    ctx.voice_client.source.volume = volume / 100
    await ctx.send(f'Громкость установлена на {volume}%')

# Запуск бота
bot.run(TOKEN) 