import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import yt_dlp
from datetime import datetime, UTC
import random
from collections import deque
import logging
import wavelink
from typing import Optional

# Настройка логирования
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger('music_bot')

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
    'cachedir': False
}

# Максимально простые настройки FFmpeg для Replit
ffmpeg_options = {
    'before_options': '-loglevel panic',
    'options': '-vn'
}

# Создаем один экземпляр YoutubeDL для всего приложения
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# Добавляем функцию для логирования ошибок
def log_error(error_message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f%z")
    error_log = f"[ERROR] {timestamp}: {error_message}"
    print(error_log)
    logger.error(error_message)

class MusicQueue:
    def __init__(self):
        self.queue = []
        self.current = None
        self.loop = False
        self.volume = 0.5

    async def add(self, track):
        """Добавить трек в очередь"""
        position = len(self.queue)
        self.queue.append(track)
        return position

    async def next(self):
        """Получить следующий трек из очереди"""
        if not self.queue:
            return None
            
        # Если включен режим повтора, возвращаем текущий трек
        if self.loop and self.current:
            return self.current
            
        # Иначе берем следующий трек из очереди
        track = self.queue.pop(0)
        self.current = track
        return track

    async def skip(self):
        """Пропустить текущий трек"""
        if not self.queue:
            return None
        return await self.next()

    async def clear(self):
        """Очистить очередь"""
        self.queue.clear()
        self.current = None

    def get_queue_list(self):
        """Получить список треков в очереди"""
        return self.queue

# Создание экземпляра бота
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Словарь для хранения очередей для каждого сервера
queues = {}

def get_queue(guild_id):
    """Получить очередь для сервера по ID"""
    if guild_id not in queues:
        queues[guild_id] = MusicQueue()
    return queues[guild_id]

# Создание embed-сообщений
def create_music_embed(title, description, color=discord.Color.blue(), thumbnail=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text=f"Музыкальный бот • {datetime.now().strftime('%H:%M:%S')}")
    return embed

# Событие готовности бота
@bot.event
async def on_ready():
    print(f'Бот {bot.user} готов к работе!')
    await bot.tree.sync()
    check_empty_channels.start()
    
    # Настройка Wavelink
    nodes = [
        wavelink.Node(
            uri='wss://lava.link:80',  # Публичный Lavalink сервер
            password='youshallnotpass',
            secure=False
        )
    ]
    await wavelink.Pool.connect(nodes=nodes, client=bot)
    print(f'Подключен к серверу Lavalink')

# Проверяет и отключается от пустых голосовых каналов
@tasks.loop(minutes=5)
async def check_empty_channels():
    for guild in bot.guilds:
        await check_empty_voice_channel(guild)

# Проверка и отключение от пустого голосового канала
async def check_empty_voice_channel(guild):
    try:
        if guild.voice_client and guild.voice_client.is_connected():
            members = guild.voice_client.channel.members
            # Если в канале только бот или вообще никого
            if len(members) <= 1:
                await guild.voice_client.disconnect()
                queue = get_queue(guild.id)
                await queue.clear()
    except Exception as e:
        log_error(f"Ошибка при проверке пустого канала: {e}")

# Команда для воспроизведения музыки
@bot.tree.command(name="play", description="Проигрывание музыки из YouTube")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Вы должны быть в голосовом канале!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return

    await interaction.response.defer()
    
    try:
        # Подключение к голосовому каналу
        if not interaction.guild.voice_client:
            voice_client = await interaction.user.voice.channel.connect(cls=wavelink.Player)
        else:
            voice_client = interaction.guild.voice_client
            if voice_client.channel != interaction.user.voice.channel:
                await voice_client.move_to(interaction.user.voice.channel)
        
        # Получение плеера и очереди
        player = voice_client
        queue = get_queue(interaction.guild_id)
        
        # Поиск трека
        tracks = await wavelink.Playable.search(query)
        if not tracks:
            await interaction.followup.send(
                embed=create_music_embed(
                    "❌ Ошибка",
                    "Не удалось найти треки по вашему запросу",
                    color=discord.Color.red()
                )
            )
            return
            
        track = tracks[0]  # Берем первый найденный трек
        
        if not player.playing:
            # Начинаем воспроизведение если ничего не играет
            await player.play(track, volume=int(queue.volume * 100))
            queue.current = track
            
            embed = create_music_embed(
                "🎵 Сейчас играет",
                f"**{track.title}**\n"
                f"👤 Исполнитель: {track.author}\n"
                f"⏱️ Длительность: {int(track.length // 60000)}:{int((track.length % 60000) // 1000):02d}",
                thumbnail=track.artwork
            )
            await interaction.followup.send(embed=embed)
        else:
            # Добавляем в очередь если уже что-то играет
            position = await queue.add(track)
            embed = create_music_embed(
                "📝 Добавлено в очередь",
                f"**{track.title}**\n"
                f"👤 Исполнитель: {track.author}\n"
                f"⏱️ Длительность: {int(track.length // 60000)}:{int((track.length % 60000) // 1000):02d}\n"
                f"📊 Позиция в очереди: {position + 1}",
                thumbnail=track.artwork
            )
            await interaction.followup.send(embed=embed)
            
    except Exception as e:
        log_error(f"Ошибка при воспроизведении: {str(e)}")
        await interaction.followup.send(
            embed=create_music_embed(
                "❌ Ошибка воспроизведения",
                f"```{str(e)}```\nПопробуйте другой запрос или повторите позже.",
                color=discord.Color.red()
            )
        )

# Обработка события окончания трека
@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player = payload.player
    guild = player.guild
    
    if not guild:
        return
        
    try:
        queue = get_queue(guild.id)
        next_track = await queue.next()
        
        if next_track:
            # Воспроизводим следующий трек из очереди
            await player.play(next_track, volume=int(queue.volume * 100))
            
            # Находим текстовый канал для отправки уведомления
            channel = None
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
                    
            if channel:
                embed = create_music_embed(
                    "🎵 Следующий трек",
                    f"**{next_track.title}**\n"
                    f"👤 Исполнитель: {next_track.author}\n"
                    f"⏱️ Длительность: {int(next_track.length // 60000)}:{int((next_track.length % 60000) // 1000):02d}",
                    thumbnail=next_track.artwork
                )
                await channel.send(embed=embed)
        else:
            # Очередь пуста
            queue.current = None
            await check_empty_voice_channel(guild)
    except Exception as e:
        log_error(f"Ошибка при обработке конца трека: {str(e)}")

# Команда для паузы
@bot.tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Сейчас ничего не воспроизводится!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    voice_client = interaction.guild.voice_client
    await voice_client.pause()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "⏸️ Пауза",
            "Воспроизведение приостановлено. Используйте `/resume` для продолжения."
        )
    )

# Команда для возобновления
@bot.tree.command(name="resume", description="Возобновить воспроизведение")
async def resume(interaction: discord.Interaction):
    if not interaction.guild.voice_client:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Бот не подключен к голосовому каналу!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    voice_client = interaction.guild.voice_client
    
    if not voice_client.is_paused():
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Воспроизведение не приостановлено!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    await voice_client.resume()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "▶️ Возобновление",
            "Воспроизведение возобновлено."
        )
    )

# Команда для пропуска трека
@bot.tree.command(name="skip", description="Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Сейчас ничего не воспроизводится!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    voice_client = interaction.guild.voice_client
    queue = get_queue(interaction.guild_id)
    
    # Получаем информацию о текущем треке перед пропуском
    current_track = queue.current
    
    # Пропускаем текущий трек
    await voice_client.stop()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "⏭️ Пропуск",
            f"Трек **{current_track.title if current_track else 'Неизвестный'}** пропущен."
        )
    )

# Команда для отображения очереди
@bot.tree.command(name="queue", description="Показать текущую очередь воспроизведения")
async def queue_command(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    queue_list = queue.get_queue_list()
    
    if not queue.current and not queue_list:
        await interaction.response.send_message(
            embed=create_music_embed(
                "📝 Очередь",
                "Очередь пуста. Добавьте треки с помощью команды `/play`"
            )
        )
        return
        
    # Формируем описание с текущим треком и очередью
    description = ""
    
    if queue.current:
        description += f"**🔊 Сейчас играет:**\n{queue.current.title} - {queue.current.author}\n\n"
        
    if queue_list:
        description += "**📋 В очереди:**\n"
        for i, track in enumerate(queue_list[:10]):
            description += f"{i+1}. {track.title} - {track.author}\n"
            
        if len(queue_list) > 10:
            description += f"\n...и еще {len(queue_list) - 10} треков"
    else:
        description += "\n**Очередь пуста**"
        
    await interaction.response.send_message(
        embed=create_music_embed(
            "📝 Очередь воспроизведения",
            description
        )
    )

# Команда для очистки очереди
@bot.tree.command(name="clear", description="Очистить очередь воспроизведения")
async def clear(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    if not queue.get_queue_list() and not queue.current:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Очередь уже пуста!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    await queue.clear()
    
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        await interaction.guild.voice_client.stop()
        
    await interaction.response.send_message(
        embed=create_music_embed(
            "🧹 Очистка",
            "Очередь воспроизведения очищена."
        )
    )

# Команда для изменения громкости
@bot.tree.command(name="volume", description="Изменить громкость воспроизведения (0-100)")
async def volume(interaction: discord.Interaction, volume: int):
    if volume < 0 or volume > 100:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Громкость должна быть от 0 до 100!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    queue = get_queue(interaction.guild_id)
    queue.volume = volume / 100
    
    if interaction.guild.voice_client and isinstance(interaction.guild.voice_client, wavelink.Player):
        await interaction.guild.voice_client.set_volume(volume)
        
    await interaction.response.send_message(
        embed=create_music_embed(
            "🔊 Громкость",
            f"Громкость установлена на {volume}%"
        )
    )

# Команда для включения/выключения режима повтора
@bot.tree.command(name="loop", description="Включить/выключить повтор текущего трека")
async def loop(interaction: discord.Interaction):
    queue = get_queue(interaction.guild_id)
    
    # Меняем режим повтора на противоположный
    queue.loop = not queue.loop
    
    status = "включен" if queue.loop else "выключен"
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "🔄 Режим повтора",
            f"Режим повтора {status}"
        )
    )

# Команда для отключения от голосового канала
@bot.tree.command(name="disconnect", description="Отключиться от голосового канала")
async def disconnect(interaction: discord.Interaction):
    if not interaction.guild.voice_client:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Бот не подключен к голосовому каналу!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    # Очищаем очередь и отключаемся
    queue = get_queue(interaction.guild_id)
    await queue.clear()
    
    await interaction.guild.voice_client.disconnect()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "👋 Отключение",
            "Бот отключен от голосового канала."
        )
    )

# Запуск бота
bot.run(TOKEN) 