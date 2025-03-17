import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
from datetime import datetime, UTC
import random
from collections import deque

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Настройки yt-dlp
ytdl_format_options = {
    'format': 'bestaudio',
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
    'prefer_ffmpeg': True
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 0 -loglevel error',
    'options': '-vn -acodec pcm_s16le -ar 48000 -ac 2 -b:a 192k'
}

# Создаем один экземпляр YoutubeDL для всего приложения
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# Добавляем функцию для логирования ошибок
def log_error(error):
    print(f"[ERROR] {datetime.now(UTC)}: {str(error)}")

class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.loop = False
        self.volume = 0.5
        self._lock = asyncio.Lock()  # Добавляем блокировку для потокобезопасности

    async def add(self, track):
        async with self._lock:
            self.queue.append(track)
            return len(self.queue) - 1

    async def next(self):
        async with self._lock:
            if self.loop and self.current:
                return self.current
            if self.queue:
                self.current = self.queue.popleft()
                return self.current
            self.current = None
            return None

    async def shuffle(self):
        async with self._lock:
            queue_list = list(self.queue)
            random.shuffle(queue_list)
            self.queue = deque(queue_list)

    async def clear(self):
        async with self._lock:
            self.queue.clear()
            self.current = None
            self.loop = False

    @property
    def is_empty(self):
        return not self.current and not self.queue

    def get_queue_info(self):
        return {
            'current': self.current,
            'queue_length': len(self.queue),
            'is_looping': self.loop
        }

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
    async def from_url(cls, url, *, loop=None, stream=True):  # Всегда используем stream=True
        loop = loop or asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            
            if not data:
                raise Exception("Не удалось получить данные о видео")

            if 'entries' in data:
                data = data['entries'][0]

            # Получаем прямую ссылку на аудио
            filename = data['url']
            
            # Создаем источник аудио
            try:
                source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
                return cls(source, data=data)
            except Exception as e:
                log_error(f"Ошибка FFmpeg: {str(e)}")
                raise Exception(f"Ошибка при обработке аудио: {str(e)}")

        except Exception as e:
            log_error(f"Общая ошибка: {str(e)}")
            raise Exception(str(e))

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
        timestamp=datetime.now(UTC)
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="Музыкальный бот")
    return embed

async def check_empty_voice_channel(guild):
    if not guild.voice_client or not guild.voice_client.is_connected():
        return

    if len(guild.voice_client.channel.members) <= 1:
        await asyncio.sleep(300)  # Ждем 5 минут
        
        # Проверяем, что клиент все еще существует и подключен
        if not guild.voice_client or not guild.voice_client.is_connected():
            return
            
        if len(guild.voice_client.channel.members) <= 1:
            try:
                await guild.voice_client.disconnect()
                queue = get_queue(guild.id)
                queue.clear()
                
                # Найдем первый доступный текстовый канал
                text_channel = None
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        text_channel = channel
                        break
                
                if text_channel:
                    embed = create_music_embed(
                        "👋 Отключение",
                        "Бот отключен из-за отсутствия слушателей",
                        color=discord.Color.red()
                    )
                    await text_channel.send(embed=embed)
            except Exception as e:
                print(f"Ошибка при отключении: {e}")

async def cleanup_player(voice_client):
    try:
        if voice_client and voice_client.is_playing():
            voice_client.stop()
        await asyncio.sleep(0.5)  # Даем время на корректное завершение
    except Exception as e:
        print(f"Ошибка при очистке плеера: {e}")

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

async def safe_play(voice_client, player, after_callback):
    """Безопасное воспроизведение с предварительной остановкой."""
    try:
        # Проверяем подключение
        if not voice_client or not voice_client.is_connected():
            raise Exception("Бот не подключен к голосовому каналу")

        # Останавливаем текущее воспроизведение
        if voice_client.is_playing():
            voice_client.stop()
            await asyncio.sleep(1)

        # Начинаем воспроизведение
        voice_client.play(player, after=after_callback)
        
    except Exception as e:
        log_error(f"Ошибка при воспроизведении: {str(e)}")
        raise e

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

        # Проверяем подключение
        if not voice_client.is_connected():
            voice_client = await interaction.user.voice.channel.connect()

        # Получение плеера
        player = await YTDLSource.from_url(url, loop=bot.loop)
        queue = get_queue(interaction.guild_id)
        
        if not voice_client.is_playing():
            await safe_play(
                voice_client, 
                player, 
                lambda e: asyncio.run_coroutine_threadsafe(check_song_end(interaction.guild), bot.loop)
            )
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
        error_message = str(e)
        if "HTTP Error 429" in error_message:
            error_message = "Слишком много запросов к YouTube. Пожалуйста, подождите немного."
        elif "Video unavailable" in error_message:
            error_message = "Видео недоступно. Возможно, оно приватное или было удалено."
        
        error_embed = create_music_embed(
            "❌ Ошибка воспроизведения",
            f"```{error_message}```\nПопробуйте другое видео или повторите попытку позже.",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=error_embed)

async def check_song_end(guild):
    queue = get_queue(guild.id)
    if queue.current:
        next_song = await queue.next()
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
    
    if queue.is_empty:
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
    
    if queue.is_empty:
        embed = create_music_embed(
            "❌ Ошибка",
            "Очередь пуста",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return
    
    await queue.shuffle()
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
    await queue.clear()
    
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

@bot.tree.command(name="stop", description="Остановить воспроизведение и очистить очередь")
async def stop(interaction: discord.Interaction):
    if not interaction.guild.voice_client:
        embed = create_music_embed(
            "❌ Ошибка",
            "Бот не находится в голосовом канале",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        return

    await cleanup_player(interaction.guild.voice_client)
    queue = get_queue(interaction.guild_id)
    await queue.clear()
    
    embed = create_music_embed(
        "⏹️ Остановлено",
        "Воспроизведение остановлено и очередь очищена",
        color=discord.Color.blue()
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

@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user:
        if after.channel is None:  # Бот был отключен
            guild = before.channel.guild
            await cleanup_player(guild.voice_client)
            queue = get_queue(guild.id)
            await queue.clear()
    elif before.channel and bot.user in before.channel.members:
        await check_empty_voice_channel(before.channel.guild)

# Запуск бота
bot.run(TOKEN) 