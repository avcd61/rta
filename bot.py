import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import asyncio
import os
from datetime import datetime
import wavelink
from typing import Optional

# Загрузка переменных окружения из .env файла
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Настройка логирования
def log_error(error_message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_log = f"[ERROR] {timestamp}: {error_message}"
    print(error_log)

# Создание экземпляра бота
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Класс для управления очередью треков
class MusicQueue:
    def __init__(self):
        self.queue = []
        self.current = None
        self.loop = False
        self.volume = 50  # 0-100

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

    async def clear(self):
        """Очистить очередь"""
        self.queue.clear()
        self.current = None

    def get_queue_list(self):
        """Получить список треков в очереди"""
        return self.queue

# Словарь для хранения очередей для каждого сервера
queues = {}

# Получение очереди для сервера
def get_queue(guild_id):
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

# Событие готовности бота
@bot.event
async def on_ready():
    print(f'Бот {bot.user} готов к работе!')
    print('------')
    try:
        print("Начинаю синхронизацию команд...")
        synced = await bot.tree.sync()
        print(f"Синхронизировано {len(synced)} команд:")
        for cmd in synced:
            print(f"  • /{cmd.name}")
    except Exception as e:
        print(f"Ошибка при синхронизации команд: {e}")
    
    # Запускаем проверку пустых каналов
    check_empty_channels.start()
    
    # Установка статуса
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening, 
            name="/play"
        )
    )
    
    # Подключение к узлам Lavalink
    # Используем несколько публичных серверов для надежности
    nodes = [
        wavelink.Node(
            uri='lavalink.oops.wtf:443',
            password='www.freelavalink.ga',
            secure=True
        ),
        wavelink.Node(
            uri='lava.link:80',
            password='anything as a password',
            secure=False
        ),
        wavelink.Node(
            uri='lavalinkinc.ml:443',
            password='incognito',
            secure=True
        )
    ]
    
    # Пробуем подключиться к узлам, используя несколько попыток
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        try:
            print(f"Попытка подключения к Lavalink (попытка {retry_count + 1})...")
            await wavelink.Pool.connect(nodes=nodes, client=bot)
            print(f"Успешно подключен к серверу Lavalink!")
            break
        except Exception as e:
            log_error(f"Ошибка подключения к Lavalink: {e}")
            retry_count += 1
            if retry_count < max_retries:
                print(f"Повторная попытка через 5 секунд...")
                await asyncio.sleep(5)
            else:
                print("Не удалось подключиться к Lavalink после нескольких попыток.")

# Проверяет и отключается от пустых голосовых каналов
@tasks.loop(minutes=5)
async def check_empty_channels():
    for guild in bot.guilds:
        await check_empty_voice_channel(guild)

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
            await player.play(next_track, volume=queue.volume)
            
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

# Команда для воспроизведения музыки
@bot.tree.command(name="play", description="Проигрывание музыки из YouTube")
async def play(interaction: discord.Interaction, запрос: str):
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

    # Отложенный ответ, так как поиск может занять время
    await interaction.response.defer()
    
    try:
        # Подключение к голосовому каналу
        if not interaction.guild.voice_client:
            player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
        else:
            player = interaction.guild.voice_client
            
            # Перемещаемся в канал пользователя, если бот в другом канале
            if player.channel != interaction.user.voice.channel:
                await player.move_to(interaction.user.voice.channel)
        
        # Получение очереди
        queue = get_queue(interaction.guild_id)
        
        # Поиск трека
        tracks = await wavelink.Playable.search(запрос)
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
            await player.play(track, volume=queue.volume)
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

# Команда для паузы
@bot.tree.command(name="pause", description="Приостановить воспроизведение")
async def pause(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not hasattr(interaction.guild.voice_client, 'playing'):
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Сейчас ничего не воспроизводится!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
        
    player = interaction.guild.voice_client
    
    if not player.playing:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Сейчас ничего не воспроизводится!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    await player.pause()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "⏸️ Пауза",
            "Воспроизведение приостановлено. Используйте `/resume` для продолжения."
        )
    )

# Команда для возобновления
@bot.tree.command(name="resume", description="Возобновить воспроизведение")
async def resume(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not hasattr(interaction.guild.voice_client, 'playing'):
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Бот не подключен к голосовому каналу!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    player = interaction.guild.voice_client
    
    if not player.paused:
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Воспроизведение не приостановлено!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    await player.resume()
    
    await interaction.response.send_message(
        embed=create_music_embed(
            "▶️ Возобновление",
            "Воспроизведение возобновлено."
        )
    )

# Команда для пропуска трека
@bot.tree.command(name="skip", description="Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    if not interaction.guild.voice_client or not hasattr(interaction.guild.voice_client, 'playing'):
        await interaction.response.send_message(
            embed=create_music_embed(
                "❌ Ошибка",
                "Сейчас ничего не воспроизводится!",
                color=discord.Color.red()
            ),
            ephemeral=True
        )
        return
    
    player = interaction.guild.voice_client
    
    if not player.playing:
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
    
    # Пропускаем текущий трек
    await player.stop()
    
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
    
    if interaction.guild.voice_client and hasattr(interaction.guild.voice_client, 'playing'):
        await interaction.guild.voice_client.stop()
        
    await interaction.response.send_message(
        embed=create_music_embed(
            "🧹 Очистка",
            "Очередь воспроизведения очищена."
        )
    )

# Команда для изменения громкости
@bot.tree.command(name="volume", description="Изменить громкость воспроизведения (0-100)")
async def volume(interaction: discord.Interaction, громкость: int):
    if громкость < 0 or громкость > 100:
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
    queue.volume = громкость
    
    if interaction.guild.voice_client and hasattr(interaction.guild.voice_client, 'playing'):
        player = interaction.guild.voice_client
        await player.set_volume(громкость)
        
    await interaction.response.send_message(
        embed=create_music_embed(
            "🔊 Громкость",
            f"Громкость установлена на {громкость}%"
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