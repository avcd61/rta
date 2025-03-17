import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import yt_dlp
import os
import asyncio
from datetime import datetime
import random
from collections import deque

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Установка лога для ошибок
def log_error(error_message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_log = f"[ERROR] {timestamp}: {error_message}"
    print(error_log)

# Настройки yt-dlp - минимальные для стабильной работы
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
    'source_address': '0.0.0.0'
}

# Максимально простые настройки FFmpeg для Replit
ffmpeg_options = {
    'before_options': '-loglevel panic',
    'options': '-vn'
}

# Создаем экземпляр ytdl
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.loop = False
        self.volume = 0.5

    async def add(self, track):
        self.queue.append(track)
        return len(self.queue) - 1

    async def next(self):
        if self.loop and self.current:
            return self.current
        if self.queue:
            self.current = self.queue.popleft()
            return self.current
        self.current = None
        return None
        
    async def clear(self):
        self.queue.clear()
        self.current = None
        self.loop = False

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Неизвестное название')
        self.url = data.get('webpage_url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', 'Неизвестный исполнитель')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        try:
            # Используем простое извлечение информации
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            
            if not data:
                raise Exception("Не удалось получить данные о видео")

            if 'entries' in data:
                data = data['entries'][0]

            # Получаем URL
            filename = data['url']
            
            # Создаем источник с минимальными настройками
            source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
            return cls(source, data=data)
            
        except Exception as e:
            log_error(f"Ошибка при получении аудио: {str(e)}")
            raise Exception(f"Не удалось воспроизвести: {str(e)}")

# Создание экземпляра бота
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Словарь для хранения очередей для каждого сервера
queues = {}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = MusicQueue()
    return queues[guild_id]

def create_music_embed(title, description, color=discord.Color.blue(), thumbnail=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text=f"Музыкальный бот • {datetime.now().strftime('%H:%M:%S')}")
    return embed

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

# Запускаем воспроизведение с минимальными настройками
async def play_audio(voice_client, player, after_callback):
    try:
        if not voice_client or not voice_client.is_connected():
            return
            
        if voice_client.is_playing():
            voice_client.stop()
            
        # Используем простой callback
        def simple_callback(error):
            if error:
                log_error(f"Ошибка: {str(error)}")
            
            # Запускаем следующий шаг как задачу
            asyncio.run_coroutine_threadsafe(after_callback(), bot.loop)
            
        voice_client.play(player, after=simple_callback)
    except Exception as e:
        log_error(f"Ошибка воспроизведения: {str(e)}")

@bot.event
async def on_ready():
    print(f'Бот {bot.user} готов к работе!')
    await bot.tree.sync()
    check_empty_channels.start()
    
    # Установка статуса
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening, 
            name="/play"
        )
    )

# Проверяет и отключается от пустых голосовых каналов
@tasks.loop(minutes=5)
async def check_empty_channels():
    for guild in bot.guilds:
        await check_empty_voice_channel(guild)

@bot.tree.command(name="play", description="Проигрывание музыки из YouTube")
async def play(interaction: discord.Interaction, url: str):
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
            voice_client = await interaction.user.voice.channel.connect()
        else:
            voice_client = interaction.guild.voice_client
            if voice_client.channel != interaction.user.voice.channel:
                await voice_client.move_to(interaction.user.voice.channel)

        # Получение плеера и очереди
        queue = get_queue(interaction.guild_id)
        player = await YTDLSource.from_url(url, loop=bot.loop)
        player.volume = queue.volume
        
        # Определяем callback для завершения песни
        async def song_finished():
            await check_song_end(interaction.guild)
        
        if not voice_client.is_playing():
            # Воспроизводим первую песню
            await play_audio(voice_client, player, song_finished)
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
            # Добавляем в очередь
            position = await queue.add(player)
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
        log_error(f"Ошибка при воспроизведении: {str(e)}")
        await interaction.followup.send(
            embed=create_music_embed(
                "❌ Ошибка воспроизведения",
                f"```{str(e)}```\nПопробуйте другой запрос или повторите позже.",
                color=discord.Color.red()
            )
        )

async def check_song_end(guild):
    try:
        queue = get_queue(guild.id)
        
        # Получаем следующую песню из очереди
        next_track = await queue.next()
        
        # Проверяем, что у нас есть голосовой клиент и он подключен
        if not guild.voice_client or not guild.voice_client.is_connected():
            return
            
        if next_track:
            # Определяем callback для следующей песни
            async def next_song_finished():
                await check_song_end(guild)
                
            # Воспроизводим следующую песню
            await play_audio(guild.voice_client, next_track, next_song_finished)
            
            # Находим подходящий текстовый канал для уведомления
            channel = None
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
            
            if channel:
                embed = create_music_embed(
                    "🎵 Следующий трек",
                    f"**{next_track.title}**\n"
                    f"👤 Исполнитель: {next_track.uploader}\n"
                    f"⏱️ Длительность: {next_track.duration//60}:{next_track.duration%60:02d}",
                    thumbnail=next_track.thumbnail
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
    
    interaction.guild.voice_client.pause()
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
    
    if not interaction.guild.voice_client.is_paused():
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Воспроизведение не приостановлено!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    interaction.guild.voice_client.resume()
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
    
    queue = get_queue(interaction.guild_id)
    current_track = queue.current
    
    interaction.guild.voice_client.stop()
    
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
    
    if not queue.current and not queue.queue:
        await interaction.response.send_message(
            embed=create_music_embed(
                "📝 Очередь",
                "Очередь пуста. Добавьте треки с помощью команды `/play`"
            )
        )
        return
    
    description = ""
    
    if queue.current:
        description += f"**🔊 Сейчас играет:**\n{queue.current.title} - {queue.current.uploader}\n\n"
    
    if queue.queue:
        description += "**📋 В очереди:**\n"
        items = list(queue.queue)
        for i, track in enumerate(items[:10]):
            description += f"{i+1}. {track.title} - {track.uploader}\n"
        
        if len(items) > 10:
            description += f"\n...и еще {len(items) - 10} треков"
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
    
    if not queue.queue and not queue.current:
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
        interaction.guild.voice_client.stop()
    
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
    
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.source.volume = queue.volume
    
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