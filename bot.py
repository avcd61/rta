import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from datetime import datetime
import random
from collections import deque

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Настройки yt-dlp
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
    'force-ipv4': True,
    'cachedir': False,
}

ffmpeg_options = {
    'options': '-vn -b:a 128k',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.loop = False
        self.volume = 0.5

    def add(self, track):
        self.queue.append(track)
        return len(self.queue) - 1

    def next(self):
        if self.loop:
            return self.current
        if self.queue:
            self.current = self.queue.popleft()
            return self.current
        return None

    def shuffle(self):
        random.shuffle(self.queue)

    def clear(self):
        self.queue.clear()
        self.current = None

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

# Создание экземпляра бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Словарь для хранения очередей для каждого сервера
music_queues = {}

def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue()
    return music_queues[guild_id]

def create_music_embed(title, description, color=discord.Color.blue(), thumbnail=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow()
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="Музыкальный бот")
    return embed

async def check_empty_voice_channel(guild):
    if guild.voice_client and guild.voice_client.is_connected():
        if len(guild.voice_client.channel.members) <= 1:
            await asyncio.sleep(300)  # Ждем 5 минут
            if len(guild.voice_client.channel.members) <= 1:
                await guild.voice_client.disconnect()
                queue = get_queue(guild.id)
                queue.clear()
                channel = guild.text_channels[0]  # Отправляем сообщение в первый текстовый канал
                embed = create_music_embed(
                    "👋 Отключение",
                    "Бот отключен из-за отсутствия слушателей",
                    color=discord.Color.red()
                )
                await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f'{bot.user.name} подключился к Discord!')
    print(f'ID бота: {bot.user.id}')
    print('------')
    
    # Установка статуса бота
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="музыку | /play"
        )
    )
    
    # Синхронизация слэш-команд
    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")

@bot.tree.command(name="play", description="Проигрывание музыки из YouTube")
async def play(interaction: discord.Interaction, url: str):
    if not interaction.user.voice:
        await interaction.response.send_message(
            '❌ Вы должны быть в голосовом канале, чтобы использовать эту команду!',
            ephemeral=True
        )
        return

    channel = interaction.user.voice.channel
    
    if interaction.guild.voice_client is None:
        await channel.connect()
    elif interaction.guild.voice_client.channel != channel:
        await interaction.guild.voice_client.move_to(channel)
    
    await interaction.response.defer()
    
    try:
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        queue = get_queue(interaction.guild_id)
        
        if not interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(
                check_song_end(interaction.guild), bot.loop
            ))
            queue.current = player
            
            embed = create_music_embed(
                "🎵 Сейчас играет",
                f"**{player.title}**\n"
                f"👤 Исполнитель: {player.uploader}\n"
                f"⏱️ Длительность: {player.duration//60}:{player.duration%60:02d}",
                thumbnail=player.thumbnail
            )
            await interaction.followup.send(embed=embed)
        else:
            position = queue.add(player)
            embed = create_music_embed(
                "📝 Добавлено в очередь",
                f"**{player.title}**\n"
                f"👤 Исполнитель: {player.uploader}\n"
                f"⏱️ Длительность: {player.duration//60}:{player.duration%60:02d}\n"
                f"📊 Позиция в очереди: {position + 1}",
                thumbnail=player.thumbnail
            )
            await interaction.followup.send(embed=embed)
            
    except Exception as e:
        error_embed = create_music_embed(
            "❌ Ошибка",
            f"Произошла ошибка при воспроизведении:\n{str(e)}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=error_embed)

async def check_song_end(guild):
    queue = get_queue(guild.id)
    if queue.current:
        next_song = queue.next()
        if next_song:
            guild.voice_client.play(next_song, after=lambda e: asyncio.run_coroutine_threadsafe(
                check_song_end(guild), bot.loop
            ))
            queue.current = next_song
            channel = guild.text_channels[0]
            embed = create_music_embed(
                "🎵 Следующий трек",
                f"**{next_song.title}**\n"
                f"👤 Исполнитель: {next_song.uploader}\n"
                f"⏱️ Длительность: {next_song.duration//60}:{next_song.duration%60:02d}",
                thumbnail=next_song.thumbnail
            )
            await channel.send(embed=embed)
        else:
            queue.current = None
            await asyncio.sleep(1)
            await check_empty_voice_channel(guild)

@bot.tree.command(name="queue", description="Показать текущую очередь воспроизведения")
async def queue(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if not queue.current and not queue.queue:
        embed = create_music_embed(
            "📋 Очередь пуста",
            "Нет треков в очереди",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    description = ""
    if queue.current:
        description += f"**Сейчас играет:**\n"
        description += f"🎵 {queue.current.title}\n"
        description += f"👤 {queue.current.uploader}\n\n"
    
    if queue.queue:
        description += "**Очередь:**\n"
        for i, track in enumerate(queue.queue, 1):
            description += f"{i}. {track.title} - {track.uploader}\n"
    
    embed = create_music_embed(
        "📋 Очередь воспроизведения",
        description,
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="skip", description="Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
        embed = create_music_embed(
            "❌ Ошибка",
            "Сейчас ничего не играет",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    interaction.guild.voice_client.stop()
    embed = create_music_embed(
        "⏭️ Пропуск",
        "Трек пропущен",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="shuffle", description="Перемешать очередь")
async def shuffle(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if not queue.queue:
        embed = create_music_embed(
            "❌ Ошибка",
            "Очередь пуста",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    queue.shuffle()
    embed = create_music_embed(
        "🔀 Перемешивание",
        "Очередь перемешана",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="loop", description="Включить/выключить повтор текущего трека")
async def loop(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    queue.loop = not queue.loop
    
    status = "включен" if queue.loop else "выключен"
    embed = create_music_embed(
        "🔁 Режим повтора",
        f"Повтор текущего трека {status}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clear", description="Очистить очередь")
async def clear(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    queue.clear()
    
    embed = create_music_embed(
        "🗑️ Очистка",
        "Очередь очищена",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pause", description="Приостановить проигрывание")
async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        embed = create_music_embed(
            "⏸️ Пауза",
            "Воспроизведение приостановлено",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_music_embed(
            "❌ Ошибка",
            "Нет проигрываемой музыки для паузы",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="resume", description="Возобновить проигрывание")
async def resume(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        embed = create_music_embed(
            "▶️ Воспроизведение",
            "Воспроизведение возобновлено",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_music_embed(
            "❌ Ошибка",
            "Музыка не на паузе",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="stop", description="Остановить проигрывание и выйти из канала")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        queue = get_queue(interaction.guild_id)
        queue.clear()
        await interaction.guild.voice_client.disconnect()
        embed = create_music_embed(
            "⏹️ Остановка",
            "Воспроизведение остановлено и бот отключен от канала",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = create_music_embed(
            "❌ Ошибка",
            "Бот не подключен к голосовому каналу",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="volume", description="Регулировать громкость (0-100)")
async def volume(interaction: discord.Interaction, volume: int):
    if interaction.guild.voice_client is None:
        embed = create_music_embed(
            "❌ Ошибка",
            "Бот не подключен к голосовому каналу",
            color=discord.Color.red()
        )
        return await interaction.response.send_message(embed=embed)
    
    volume = max(0, min(100, volume))
    interaction.guild.voice_client.source.volume = volume / 100
    queue = get_queue(interaction.guild_id)
    queue.volume = volume / 100
    
    # Создаем визуальный индикатор громкости
    bar_length = 20
    filled = int(volume * bar_length // 100)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    embed = create_music_embed(
        "🔊 Громкость",
        f"Установлена громкость: {volume}%\n{bar}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

# Запуск бота
bot.run(TOKEN) 