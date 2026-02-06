import asyncio
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import aiofiles

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ChatMemberAdministrator, InputFile
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Конфигурация
TOKEN = os.getenv("BOT_TOKEN", "8022954037:AAHH75JVSpIBXGfmgV3PCZcR2h85Y5qSI5A")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# Настройки базы данных
DATABASE_FILE = os.getenv("DATABASE_FILE", "game_database.db")
WAR_IMAGES_FOLDER = "war_images"

# Создаем папку для изображений войны, если она не существует
if not os.path.exists(WAR_IMAGES_FOLDER):
    os.makedirs(WAR_IMAGES_FOLDER)
    print(f"📁 Создана папка для изображений войны: {WAR_IMAGES_FOLDER}")
    print(f"📝 Поместите изображения войны в папку {WAR_IMAGES_FOLDER}/")

@dataclass
class Country:
    """Класс страны"""
    name: str
    emoji: str
    base_income: float  # Пассивный доход в секунду
    army_cost: int = 1000  # Стоимость улучшения армии
    city_cost: int = 5000  # Стоимость улучшения города
    war_image: str = "war_default.jpg"  # Изображение для войны

COUNTRIES = {
    "russia": Country("Россия", "🇷🇺", 10.0, war_image="russia_war.jpg"),
    "ukraine": Country("Украина", "🇺🇦", 8.0, war_image="ukraine_war.jpg"),
    "turkey": Country("Турция", "🇹🇷", 7.0, war_image="turkey_war.jpg"),
    "sweden": Country("Швеция", "🇸🇪", 6.0, war_image="sweden_war.jpg"),
    "finland": Country("Финляндия", "🇫🇮", 5.0, war_image="finland_war.jpg"),
    "spain": Country("Испания", "🇪🇸", 9.0, war_image="spain_war.jpg"),
}

@dataclass
class Player:
    """Класс игрока"""
    user_id: int
    username: str
    country: str
    money: float = 1000.0
    army_level: int = 1
    city_level: int = 1
    last_income: datetime = field(default_factory=datetime.now)
    wins: int = 0
    losses: int = 0

class TransferData:
    """Класс для временного хранения данных перевода"""
    def __init__(self):
        self.transfers = {}  # user_id -> (target_id, transfer_type, chat_id)

transfer_data = TransferData()

# Глобальные переменные
bot: Optional[Bot] = None

# ========== СИНХРОННАЯ БАЗА ДАННЫХ ==========

def init_database():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Таблица игр
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS games (
        chat_id INTEGER PRIMARY KEY,
        creator_id INTEGER,
        war_active BOOLEAN DEFAULT 0,
        war_participants TEXT,
        war_start_time TEXT,
        last_war TEXT
    )
    ''')
    
    # Таблица игроков
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        country TEXT,
        money REAL DEFAULT 1000.0,
        army_level INTEGER DEFAULT 1,
        city_level INTEGER DEFAULT 1,
        last_income TEXT,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        chat_id INTEGER,
        FOREIGN KEY (chat_id) REFERENCES games (chat_id),
        UNIQUE(user_id, chat_id)
    )
    ''')
    
    # Индексы для ускорения поиска
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON players(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_id ON players(chat_id)')
    
    conn.commit()
    conn.close()
    print(f"✅ База данных инициализирована: {DATABASE_FILE}")

async def save_game(chat_id: int, creator_id: int, war_active: bool = False, 
                   war_participants: List[int] = None, war_start_time: Optional[datetime] = None,
                   last_war: Optional[datetime] = None):
    """Сохранить или обновить игру"""
    await asyncio.get_event_loop().run_in_executor(None, lambda: _save_game_sync(
        chat_id, creator_id, war_active, war_participants, war_start_time, last_war
    ))

def _save_game_sync(chat_id: int, creator_id: int, war_active: bool = False,
                   war_participants: List[int] = None, war_start_time: Optional[datetime] = None,
                   last_war: Optional[datetime] = None):
    """Синхронная версия сохранения игры"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    war_participants_str = json.dumps(war_participants) if war_participants else "[]"
    war_start_time_str = war_start_time.isoformat() if war_start_time else None
    last_war_str = last_war.isoformat() if last_war else None
    
    cursor.execute('''
    INSERT OR REPLACE INTO games (chat_id, creator_id, war_active, war_participants, war_start_time, last_war)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, creator_id, war_active, war_participants_str, war_start_time_str, last_war_str))
    
    conn.commit()
    conn.close()

async def save_player(player: Player, chat_id: int):
    """Сохранить или обновить игрока"""
    await asyncio.get_event_loop().run_in_executor(None, lambda: _save_player_sync(player, chat_id))

def _save_player_sync(player: Player, chat_id: int):
    """Синхронная версия сохранения игрока"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT OR REPLACE INTO players 
    (user_id, username, country, money, army_level, city_level, last_income, wins, losses, chat_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        player.user_id, player.username, player.country, player.money,
        player.army_level, player.city_level, player.last_income.isoformat(),
        player.wins, player.losses, chat_id
    ))
    
    conn.commit()
    conn.close()

async def load_game(chat_id: int) -> Optional[Dict]:
    """Загрузить игру по chat_id"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _load_game_sync(chat_id))

def _load_game_sync(chat_id: int) -> Optional[Dict]:
    """Синхронная версия загрузки игры"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM games WHERE chat_id = ?', (chat_id,))
    game_data = cursor.fetchone()
    conn.close()
    
    if not game_data:
        return None
    
    # Преобразуем данные игры
    game = {
        "chat_id": game_data[0],
        "creator_id": game_data[1],
        "war_active": bool(game_data[2]),
        "war_participants": json.loads(game_data[3]) if game_data[3] else [],
        "war_start_time": datetime.fromisoformat(game_data[4]) if game_data[4] else None,
        "last_war": datetime.fromisoformat(game_data[5]) if game_data[5] else None
    }
    
    return game

async def load_player(user_id: int, chat_id: int) -> Optional[Player]:
    """Загрузить игрока по user_id и chat_id"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _load_player_sync(user_id, chat_id))

def _load_player_sync(user_id: int, chat_id: int) -> Optional[Player]:
    """Синхронная версия загрузки игрока"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT * FROM players WHERE user_id = ? AND chat_id = ?
    ''', (user_id, chat_id))
    
    player_data = cursor.fetchone()
    conn.close()
    
    if not player_data:
        return None
    
    # player_data: (id, user_id, username, country, money, army_level, city_level, last_income, wins, losses, chat_id)
    return Player(
        user_id=player_data[1],
        username=player_data[2],
        country=player_data[3],
        money=player_data[4],
        army_level=player_data[5],
        city_level=player_data[6],
        last_income=datetime.fromisoformat(player_data[7]),
        wins=player_data[8],
        losses=player_data[9]
    )

async def load_all_players(chat_id: int) -> Dict[int, Player]:
    """Загрузить всех игроков в игре"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _load_all_players_sync(chat_id))

def _load_all_players_sync(chat_id: int) -> Dict[int, Player]:
    """Синхронная версия загрузки всех игроков"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM players WHERE chat_id = ?', (chat_id,))
    players_data = cursor.fetchall()
    conn.close()
    
    players = {}
    for player_data in players_data:
        player = Player(
            user_id=player_data[1],
            username=player_data[2],
            country=player_data[3],
            money=player_data[4],
            army_level=player_data[5],
            city_level=player_data[6],
            last_income=datetime.fromisoformat(player_data[7]),
            wins=player_data[8],
            losses=player_data[9]
        )
        players[player.user_id] = player
    
    return players

async def get_game_players_count(chat_id: int) -> int:
    """Получить количество игроков в игре"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _get_game_players_count_sync(chat_id))

def _get_game_players_count_sync(chat_id: int) -> int:
    """Синхронная версия получения количества игроков"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM players WHERE chat_id = ?', (chat_id,))
    count = cursor.fetchone()[0]
    conn.close()
    
    return count

async def delete_game(chat_id: int):
    """Удалить игру и всех игроков"""
    await asyncio.get_event_loop().run_in_executor(None, lambda: _delete_game_sync(chat_id))

def _delete_game_sync(chat_id: int):
    """Синхронная версия удаления игры"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM players WHERE chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM games WHERE chat_id = ?', (chat_id,))
    
    conn.commit()
    conn.close()

async def find_player_game(user_id: int) -> Tuple[Optional[int], Optional[Dict]]:
    """Найти игру, в которой находится игрок"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _find_player_game_sync(user_id))

def _find_player_game_sync(user_id: int) -> Tuple[Optional[int], Optional[Dict]]:
    """Синхронная версия поиска игры игрока"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT chat_id FROM players WHERE user_id = ? LIMIT 1', (user_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return None, None
    
    chat_id = result[0]
    
    # Загружаем игру
    cursor.execute('SELECT * FROM games WHERE chat_id = ?', (chat_id,))
    game_data = cursor.fetchone()
    conn.close()
    
    if not game_data:
        return chat_id, None
    
    game = {
        "chat_id": game_data[0],
        "creator_id": game_data[1],
        "war_active": bool(game_data[2]),
        "war_participants": json.loads(game_data[3]) if game_data[3] else [],
        "war_start_time": datetime.fromisoformat(game_data[4]) if game_data[4] else None,
        "last_war": datetime.fromisoformat(game_data[5]) if game_data[5] else None
    }
    
    return chat_id, game

async def get_all_games() -> Dict[int, Dict]:
    """Получить все активные игры"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: _get_all_games_sync())

def _get_all_games_sync() -> Dict[int, Dict]:
    """Синхронная версия получения всех игр"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM games')
    games_data = cursor.fetchall()
    conn.close()
    
    games = {}
    for game_data in games_data:
        game = {
            "chat_id": game_data[0],
            "creator_id": game_data[1],
            "war_active": bool(game_data[2]),
            "war_participants": json.loads(game_data[3]) if game_data[3] else [],
            "war_start_time": datetime.fromisoformat(game_data[4]) if game_data[4] else None,
            "last_war": datetime.fromisoformat(game_data[5]) if game_data[5] else None
        }
        games[game["chat_id"]] = game
    
    return games

async def update_player_income_in_db(user_id: int, chat_id: int) -> float:
    """Обновить доход конкретного игрока и вернуть начисленную сумму"""
    return await asyncio.get_event_loop().run_in_executor(None, 
        lambda: _update_player_income_in_db_sync(user_id, chat_id))

def _update_player_income_in_db_sync(user_id: int, chat_id: int) -> float:
    """Синхронная версия обновления дохода"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Загружаем игрока
        cursor.execute('''
        SELECT * FROM players WHERE user_id = ? AND chat_id = ?
        ''', (user_id, chat_id))
        
        player_data = cursor.fetchone()
        
        if not player_data:
            print(f"❌ Игрок {user_id} не найден в чате {chat_id}")
            conn.close()
            return 0
        
        player = Player(
            user_id=player_data[1],
            username=player_data[2],
            country=player_data[3],
            money=player_data[4],
            army_level=player_data[5],
            city_level=player_data[6],
            last_income=datetime.fromisoformat(player_data[7]),
            wins=player_data[8],
            losses=player_data[9]
        )
        
        current_time = datetime.now()
        time_diff = (current_time - player.last_income).total_seconds()
        
        print(f"🔄 Обновление дохода для {player.username} (ID: {user_id})")
        print(f"   Время последнего дохода: {player.last_income}")
        print(f"   Текущее время: {current_time}")
        print(f"   Разница: {time_diff:.1f} секунд")
        print(f"   Текущие деньги: {player.money}")
        print(f"   Страна: {player.country}")
        print(f"   Уровень города: {player.city_level}")
        
        if time_diff > 0:
            country = COUNTRIES.get(player.country)
            if country:
                # Рассчитываем доход
                income = country.base_income * player.city_level * time_diff
                income = round(income, 2)  # Округляем до 2 знаков
                
                print(f"   Базовая ставка: {country.base_income}/сек")
                print(f"   Рассчитанный доход: {income:.2f} монет")
                
                if income > 0:
                    # Обновляем деньги игрока
                    player.money += income
                    player.last_income = current_time
                    
                    # Сохраняем обновленного игрока
                    cursor.execute('''
                    UPDATE players 
                    SET money = ?, last_income = ? 
                    WHERE user_id = ? AND chat_id = ?
                    ''', (player.money, player.last_income.isoformat(), user_id, chat_id))
                    
                    conn.commit()
                    conn.close()
                    
                    print(f"💰 Игрок {player.username} получил {income:.2f} монет")
                    print(f"   Новый баланс: {player.money:.2f}")
                    return income
                else:
                    print(f"⚠️ Рассчитанный доход 0 или меньше для {player.username}")
                    conn.close()
                    return 0
            else:
                print(f"❌ Страна {player.country} не найдена в COUNTRIES")
                conn.close()
                return 0
        else:
            print(f"⚠️ Время не изменилось для {player.username}")
            conn.close()
            return 0
    except Exception as e:
        print(f"❌ Ошибка при обновлении дохода для {user_id}: {e}")
        return 0

async def update_all_players_income_in_chat(chat_id: int):
    """Обновить доход всех игроков в чате"""
    await asyncio.get_event_loop().run_in_executor(None, 
        lambda: _update_all_players_income_in_chat_sync(chat_id))

def _update_all_players_income_in_chat_sync(chat_id: int):
    """Синхронная версия обновления дохода всех игроков"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Проверяем, есть ли активная война
        cursor.execute('SELECT war_active FROM games WHERE chat_id = ?', (chat_id,))
        game_data = cursor.fetchone()
        
        if game_data and bool(game_data[0]):  # Если идет война
            print(f"⚔️ Пропускаем чат {chat_id} - идет война")
            conn.close()
            return
        
        # Загружаем всех игроков
        cursor.execute('SELECT * FROM players WHERE chat_id = ?', (chat_id,))
        players_data = cursor.fetchall()
        
        if not players_data:
            print(f"⚠️ В чате {chat_id} нет игроков")
            conn.close()
            return
        
        current_time = datetime.now()
        total_income = 0
        
        print(f"🔍 Обновление дохода в чате {chat_id} для {len(players_data)} игроков")
        
        for player_data in players_data:
            player = Player(
                user_id=player_data[1],
                username=player_data[2],
                country=player_data[3],
                money=player_data[4],
                army_level=player_data[5],
                city_level=player_data[6],
                last_income=datetime.fromisoformat(player_data[7]),
                wins=player_data[8],
                losses=player_data[9]
            )
            
            time_diff = (current_time - player.last_income).total_seconds()
            
            if time_diff > 0:
                country = COUNTRIES.get(player.country)
                if country:
                    # Рассчитываем доход
                    income = country.base_income * player.city_level * time_diff
                    income = round(income, 2)
                    total_income += income
                    
                    if income > 0:
                        print(f"   {player.username}: +{income:.2f} монет ({time_diff:.1f} сек)")
                        
                        # Обновляем игрока в базе
                        new_money = player.money + income
                        cursor.execute('''
                        UPDATE players 
                        SET money = ?, last_income = ? 
                        WHERE user_id = ? AND chat_id = ?
                        ''', (new_money, current_time.isoformat(), player.user_id, chat_id))
        
        conn.commit()
        conn.close()
        
        if total_income > 0:
            print(f"💰 В чате {chat_id} начислено {total_income:.2f} монет")
        else:
            print(f"ℹ️ В чате {chat_id} не было начислений")
            
    except Exception as e:
        print(f"❌ Ошибка при обновлении дохода в чате {chat_id}: {e}")

async def force_update_all_incomes():
    """Принудительное обновление дохода для всех игроков"""
    print("🔄 Принудительное обновление дохода для всех игроков...")
    
    # Получаем все активные игры
    games = await get_all_games()
    
    for chat_id, game in games.items():
        if not game["war_active"]:  # Если нет активной войны
            await update_all_players_income_in_chat(chat_id)
    
    print("✅ Доход обновлен для всех игроков")

# ========== ОСНОВНЫЕ ФУНКЦИИ БОТА ==========

def get_game_keyboard(player_id: int) -> InlineKeyboardBuilder:
    """Клавиатура для игрока"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Статистика", callback_data=f"stats_{player_id}")
    builder.button(text="⚔️ Улучшить армию", callback_data=f"upgrade_army_{player_id}")
    builder.button(text="🏙️ Улучшить город", callback_data=f"upgrade_city_{player_id}")
    builder.button(text="🌍 Топ игроков", callback_data=f"top_{player_id}")
    builder.button(text="⚔️ Начать войну", callback_data=f"start_war_{player_id}")
    builder.button(text="🔄 Обновить деньги", callback_data=f"refresh_{player_id}")
    builder.button(text="🔄 Сменить страну", callback_data=f"change_country_{player_id}")
    builder.button(text="💸 Передать деньги", callback_data=f"transfer_money_{player_id}")
    builder.button(text="🎖️ Передать армию", callback_data=f"transfer_army_{player_id}")
    builder.adjust(2, 2, 2, 1, 2)
    return builder

def get_countries_keyboard() -> InlineKeyboardBuilder:
    """Клавиатура выбора страны"""
    builder = InlineKeyboardBuilder()
    for country_id, country in COUNTRIES.items():
        builder.button(text=f"{country.emoji} {country.name}", callback_data=f"country_{country_id}")
    builder.adjust(2)
    return builder

async def get_players_keyboard(chat_id: int, exclude_id: int, action: str) -> InlineKeyboardBuilder:
    """Клавиатура выбора игрока для передачи"""
    builder = InlineKeyboardBuilder()
    players = await load_all_players(chat_id)
    
    for player_id, player in players.items():
        if player_id != exclude_id:
            country = COUNTRIES.get(player.country)
            if country:
                builder.button(
                    text=f"{player.username} ({country.emoji})", 
                    callback_data=f"{action}_{player_id}"
                )
    builder.button(text="❌ Отмена", callback_data=f"cancel_{exclude_id}")
    builder.adjust(1)
    return builder

async def get_war_targets_keyboard(chat_id: int, attacker_id: int) -> InlineKeyboardBuilder:
    """Клавиатура выбора цели для войны"""
    builder = InlineKeyboardBuilder()
    players = await load_all_players(chat_id)
    
    for player_id, player in players.items():
        if player_id != attacker_id:
            country = COUNTRIES.get(player.country)
            if country:
                builder.button(
                    text=f"{player.username} ({country.emoji})", 
                    callback_data=f"wartarget_{player_id}"
                )
    builder.button(text="❌ Отмена", callback_data=f"cancel_{attacker_id}")
    builder.adjust(1)
    return builder

async def is_admin_in_chat(chat_id: int, user_id: int) -> bool:
    """Проверка, является ли пользователь администратором чата"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return isinstance(member, ChatMemberAdministrator) or member.status == "creator"
    except:
        return False

async def send_war_image(chat_id: int, attacker_country: Country, target_country: Country):
    """Отправить изображение войны"""
    try:
        # Пытаемся найти изображение атакующей страны
        attacker_image_path = os.path.join(WAR_IMAGES_FOLDER, attacker_country.war_image)
        
        # Если нет изображения для конкретной страны, используем дефолтное
        if not os.path.exists(attacker_image_path):
            # Пробуем найти любое изображение в папке
            available_images = [f for f in os.listdir(WAR_IMAGES_FOLDER) 
                              if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
            
            if available_images:
                # Используем случайное изображение
                image_name = random.choice(available_images)
                image_path = os.path.join(WAR_IMAGES_FOLDER, image_name)
            else:
                # Если нет изображений вообще, не отправляем
                print(f"⚠️ В папке {WAR_IMAGES_FOLDER} нет изображений для войны")
                return
        else:
            image_path = attacker_image_path
        
        # Отправляем изображение
        with open(image_path, 'rb') as photo:
            await bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(photo),
                caption=f"⚔️ {attacker_country.emoji} vs {target_country.emoji} ⚔️"
            )
            
    except Exception as e:
        print(f"⚠️ Ошибка при отправке изображения войны: {e}")

# ========== ОБРАБОТЧИКИ КОМАНД ==========

async def handle_start(message: Message):
    """Обработка команды /start"""
    if message.chat.type == "private":
        await message.answer("🎮 Игра доступна только в групповых чатах!\n\n"
                           "Добавьте меня в группу и используйте /game")
        return
    
    await message.answer("🎮 Для начала игры введите /game")

async def handle_game(message: Message):
    """Обработка команды /game"""
    if message.chat.type == "private":
        await message.answer("🎮 Игра доступна только в групповых чатах!")
        return
    
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    print(f"🎮 Команда /game от {message.from_user.username} (ID: {user_id}) в чате {chat_id}")
    
    # Проверяем, есть ли уже игра
    existing_game = await load_game(chat_id)
    
    if existing_game and existing_game["war_active"]:
        await message.answer("⚔️ Сейчас идет война! Подождите ее окончания.")
        return
    
    if not existing_game:
        # Создание новой игры
        await save_game(chat_id, message.from_user.id)
        await message.answer("🎮 Игра создана! Чтобы присоединиться, нажмите /join")
    else:
        # Проверка, участвует ли уже пользователь
        player = await load_player(user_id, chat_id)
        if player:
            print(f"👤 Игрок {player.username} уже в игре, обновляем меню")
            # ПРИНУДИТЕЛЬНО обновляем доход перед показом меню
            income = await update_player_income_in_db(user_id, chat_id)
            if income > 0:
                await message.answer(f"💰 Вы получили {income:.2f} монет пассивного дохода!")
            await show_player_menu(message, player)
            return
        
        await message.answer("🎮 Игра уже создана! Чтобы присоединиться, нажмите /join")

async def handle_join(message: Message):
    """Обработка команды /join"""
    if message.chat.type == "private":
        await message.answer("🎮 Игра доступна только в групповых чатах!")
        return
    
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    print(f"👤 Команда /join от {message.from_user.username} (ID: {user_id}) в чате {chat_id}")
    
    game = await load_game(chat_id)
    if not game:
        await message.answer("❌ Игра не создана! Сначала создайте игру с помощью /game")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await message.answer("⚔️ Сейчас идет война! Подождите ее окончания.")
        return
    
    # Проверка, участвует ли уже пользователь
    player = await load_player(user_id, chat_id)
    if player:
        print(f"👤 Игрок {player.username} уже в игре")
        # ПРИНУДИТЕЛЬНО обновляем доход перед показом меню
        income = await update_player_income_in_db(user_id, chat_id)
        if income > 0:
            await message.answer(f"💰 Вы получили {income:.2f} монет пассивного дохода!")
        await message.answer("✅ Вы уже в игре!")
        await show_player_menu(message, player)
        return
    
    # Выбор страны
    builder = get_countries_keyboard()
    await message.answer(
        "🌍 Выберите страну:",
        reply_markup=builder.as_markup()
    )

async def handle_country_selection(callback: CallbackQuery):
    """Обработка выбора страны"""
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    print(f"🌍 Выбор страны от {callback.from_user.username} (ID: {user_id})")
    
    # Проверяем, есть ли игра в этом чате
    game = await load_game(chat_id)
    if not game:
        # Ищем игру, где есть пользователь
        found_chat_id, found_game = await find_player_game(user_id)
        if found_game:
            chat_id = found_chat_id
            game = found_game
        else:
            await callback.answer("❌ Игра не найдена!")
            return
    
    country_id = callback.data.split('_')[1]
    
    if country_id not in COUNTRIES:
        await callback.answer("❌ Неверная страна!")
        return
    
    # Проверка, не выбрана ли страна другим игроком в этой игре
    players = await load_all_players(chat_id)
    for player in players.values():
        if player.country == country_id and player.user_id != user_id:
            await callback.answer("❌ Эта страна уже занята!")
            return
    
    # Создание игрока или обновление страны
    existing_player = await load_player(user_id, chat_id)
    
    if existing_player:
        # Смена страны существующего игрока
        existing_player.country = country_id
        await save_player(existing_player, chat_id)
        action_text = "сменили страну на"
        player = existing_player
    else:
        # Создание нового игрока
        player = Player(
            user_id=user_id,
            username=callback.from_user.username or callback.from_user.first_name,
            country=country_id,
            last_income=datetime.now()  # Устанавливаем текущее время
        )
        await save_player(player, chat_id)
        action_text = "присоединились к игре как"
    
    country = COUNTRIES[country_id]
    print(f"✅ Игрок {player.username} выбрал страну {country.name}")
    
    await callback.message.edit_text(
        f"✅ Вы {action_text} {country.emoji} {country.name}!\n\n"
        f"💰 Начальный капитал: {int(player.money)}\n"
        f"⚔️ Уровень армии: {player.army_level}\n"
        f"🏙️ Уровень города: {player.city_level}\n\n"
        f"Пассивный доход: {country.base_income * player.city_level:.1f}/сек"
    )
    
    await update_player_menu(callback.message, player)

async def update_player_menu(message: Message, player: Player):
    """Обновить меню игрока"""
    chat_id, game = await find_player_game(player.user_id)
    if not game or not chat_id:
        print(f"❌ Игра не найдена для игрока {player.username}")
        return
    
    print(f"🔄 Обновление меню для {player.username} (ID: {player.user_id})")
    
    # Загружаем обновленного игрока
    updated_player = await load_player(player.user_id, chat_id)
    if not updated_player:
        print(f"❌ Не удалось загрузить игрока {player.username}")
        return
    
    country = COUNTRIES.get(updated_player.country)
    if not country:
        print(f"❌ Страна не найдена для игрока {player.username}")
        return
    
    # Расчет дохода
    income_per_sec = country.base_income * updated_player.city_level
    army_upgrade_cost = country.army_cost * updated_player.army_level
    city_upgrade_cost = country.city_cost * updated_player.city_level
    
    text = (
        f"🌍 {country.emoji} {country.name}\n"
        f"👤 Игрок: {updated_player.username}\n"
        f"💰 Деньги: {int(updated_player.money)}\n"
        f"⚔️ Уровень армии: {updated_player.army_level}\n"
        f"🏙️ Уровень города: {updated_player.city_level}\n"
        f"📈 Пассивный доход: {income_per_sec:.1f}/сек\n"
        f"🏆 Победы: {updated_player.wins} | Поражения: {updated_player.losses}\n\n"
        f"⚔️ Улучшить армию ({army_upgrade_cost}💰)\n"
        f"🏙️ Улучшить город ({city_upgrade_cost}💰)"
    )
    
    builder = get_game_keyboard(updated_player.user_id)
    
    # Пытаемся редактировать сообщение
    try:
        await message.edit_text(text, reply_markup=builder.as_markup())
    except TelegramBadRequest as e:
        print(f"⚠️ Не удалось редактировать сообщение: {e}")
        # Если сообщение нельзя редактировать, отправляем новое
        await message.answer(text, reply_markup=builder.as_markup())

async def show_player_menu(message: Message, player: Optional[Player] = None):
    """Показать меню игрока"""
    user_id = message.from_user.id
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await message.answer("❌ Вы не в игре! Используйте /join")
        return
    
    print(f"📱 Показ меню для пользователя {user_id} в чате {chat_id}")
    
    if not player:
        player = await load_player(user_id, chat_id)
        if not player:
            await message.answer("❌ Вы не в игре! Используйте /join")
            return
    
    # Загружаем обновленного игрока
    updated_player = await load_player(user_id, chat_id)
    if not updated_player:
        await message.answer("❌ Ошибка загрузки данных!")
        return
    
    await update_player_menu(message, updated_player)

async def handle_stats(callback: CallbackQuery):
    """Обработка просмотра статистики"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"📊 Статистика запрошена пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Загружаем игрока
    player = await load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    country = COUNTRIES.get(player.country)
    if not country:
        await callback.answer("❌ Ошибка данных страны!")
        return
    
    income_per_sec = country.base_income * player.city_level
    army_upgrade_cost = country.army_cost * player.army_level
    city_upgrade_cost = country.city_cost * player.city_level
    
    text = (
        f"📊 Статистика {player.username}:\n\n"
        f"🌍 Страна: {country.emoji} {country.name}\n"
        f"💰 Деньги: {int(player.money)}\n"
        f"⚔️ Уровень армии: {player.army_level}\n"
        f"🏙️ Уровень города: {player.city_level}\n"
        f"📈 Пассивный доход: {income_per_sec:.1f}/сек\n"
        f"💵 След. улучшение армии: {army_upgrade_cost}💰\n"
        f"🏗️ След. улучшение города: {city_upgrade_cost}💰\n"
        f"🏆 Победы/Поражения: {player.wins}/{player.losses}"
    )
    
    await callback.message.edit_text(text)
    await callback.answer()

async def handle_upgrade_army(callback: CallbackQuery):
    """Обработка улучшения армии"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"⚔️ Улучшение армии запрошено пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя улучшать армию!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед улучшением
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При улучшении армии начислен доход: {income:.2f} монет")
    
    player = await load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    country = COUNTRIES.get(player.country)
    if not country:
        await callback.answer("❌ Ошибка данных страны!")
        return
    
    upgrade_cost = country.army_cost * player.army_level
    
    if player.money >= upgrade_cost:
        player.money -= upgrade_cost
        player.army_level += 1
        await save_player(player, chat_id)
        
        await callback.answer(f"✅ Армия улучшена до уровня {player.army_level}!")
        await update_player_menu(callback.message, player)
    else:
        await callback.answer(f"❌ Не хватает денег! Нужно: {upgrade_cost}💰")

async def handle_upgrade_city(callback: CallbackQuery):
    """Обработка улучшения города"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"🏙️ Улучшение города запрошено пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя улучшать город!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед улучшением
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При улучшении города начислен доход: {income:.2f} монет")
    
    player = await load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    country = COUNTRIES.get(player.country)
    if not country:
        await callback.answer("❌ Ошибка данных страны!")
        return
    
    upgrade_cost = country.city_cost * player.city_level
    
    if player.money >= upgrade_cost:
        player.money -= upgrade_cost
        player.city_level += 1
        await save_player(player, chat_id)
        
        await callback.answer(f"✅ Город улучшен до уровня {player.city_level}!")
        await update_player_menu(callback.message, player)
    else:
        await callback.answer(f"❌ Не хватает денег! Нужно: {upgrade_cost}💰")

async def handle_top(callback: CallbackQuery):
    """Обработка топа игроков"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"🌍 Топ игроков запрошен пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход для всех игроков в чате
    await update_all_players_income_in_chat(chat_id)
    
    players = await load_all_players(chat_id)
    
    if len(players) < 2:
        await callback.message.edit_text("⚠️ Для топа нужно как минимум 2 игрока!")
        return
    
    # Сортируем игроков по деньгам
    sorted_players = sorted(players.values(), key=lambda p: p.money, reverse=True)
    
    top_text = "🏆 Топ игроков:\n\n"
    for i, player in enumerate(sorted_players[:10], 1):
        country = COUNTRIES.get(player.country, Country("Неизвестно", "❓", 0))
        top_text += f"{i}. {country.emoji} {player.username}: {int(player.money)}💰 (⚔️{player.army_level} 🏙️{player.city_level})\n"
    
    await callback.message.edit_text(top_text)
    await callback.answer()

async def handle_refresh(callback: CallbackQuery):
    """Обработка обновления денег - ГЛАВНАЯ КНОПКА, КОТОРУЮ ЧИНИМ!"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"🔄 КНОПКА ОБНОВЛЕНИЯ нажата пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед обновлением
    print(f"💰 Вызываем update_player_income_in_db для {user_id}")
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 Результат update_player_income_in_db: {income:.2f} монет")
    
    player = await load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    print(f"💰 Баланс игрока после обновления: {player.money}")
    
    # Показываем обновленное меню
    await update_player_menu(callback.message, player)
    
    if income > 0:
        # Показываем всплывающее уведомление
        await callback.answer(f"✅ Вы получили {income:.2f} монет!", show_alert=True)
        print(f"✅ Показано уведомление о доходе: {income:.2f} монет")
    else:
        await callback.answer("✅ Данные обновлены!")
        print("ℹ️ Доход не начислен")

async def handle_change_country(callback: CallbackQuery):
    """Обработка смены страны"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"🔄 Смена страны запрошена пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя менять страну!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед сменой страны
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При смене страны начислен доход: {income:.2f} монет")
    
    # Показываем клавиатуру выбора страны
    builder = get_countries_keyboard()
    text = "🌍 Выберите новую страну:"
    
    if income > 0:
        text = f"💰 Вы получили {income:.2f} монет!\n\n" + text
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_start_war(callback: CallbackQuery):
    """Обработка начала войны"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"⚔️ Начало войны запрошено пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверяем, не идет ли уже война
    if game["war_active"]:
        await callback.answer("⚔️ Война уже идет! Подождите ее окончания.")
        return
    
    # Проверяем время с последней войны
    if game.get("last_war"):
        time_since_last_war = datetime.now() - game["last_war"]
        if time_since_last_war < timedelta(minutes=1):
            wait_time = 60 - int(time_since_last_war.total_seconds())
            await callback.answer(f"⏳ Следующая война возможна через {wait_time} секунд!")
            return
    
    # Проверяем количество игроков
    players_count = await get_game_players_count(chat_id)
    if players_count < 2:
        await callback.answer("⚠️ Для войны нужно как минимум 2 игрока!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед началом войны
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При начале войны начислен доход: {income:.2f} монет")
    
    # Показываем выбор цели
    builder = await get_war_targets_keyboard(chat_id, user_id)
    text = "🎯 Выберите цель для атаки:"
    
    if income > 0:
        text = f"💰 Вы получили {income:.2f} монет!\n\n" + text
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_war_target(callback: CallbackQuery):
    """Обработка выбора цели для войны"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_id = int(data[1])
    attacker_id = callback.from_user.id
    
    if attacker_id == target_id:
        await callback.answer("❌ Нельзя атаковать самого себя!")
        return
    
    print(f"🎯 Выбор цели войны: {attacker_id} -> {target_id}")
    
    chat_id, game = await find_player_game(attacker_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Игра не найдена!")
        return
    
    # Проверяем, не идет ли уже война
    if game["war_active"]:
        await callback.answer("⚔️ Война уже идет!")
        return
    
    # Загружаем игроков
    attacker = await load_player(attacker_id, chat_id)
    target = await load_player(target_id, chat_id)
    
    if not attacker or not target:
        await callback.answer("❌ Игрок не найден!")
        return
    
    attacker_country = COUNTRIES.get(attacker.country)
    target_country = COUNTRIES.get(target.country)
    
    if not attacker_country or not target_country:
        await callback.answer("❌ Ошибка данных страны!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход обоих игроков перед войной
    print(f"💰 Обновляем доход для атакующего {attacker.username}")
    income_attacker = await update_player_income_in_db(attacker_id, chat_id)
    
    print(f"💰 Обновляем доход для цели {target.username}")
    income_target = await update_player_income_in_db(target_id, chat_id)
    
    # Перезагружаем обновленных игроков
    attacker = await load_player(attacker_id, chat_id)
    target = await load_player(target_id, chat_id)
    
    # Начинаем войну
    war_start_time = datetime.now()
    game["war_active"] = True
    game["war_participants"] = [attacker_id, target_id]
    game["war_start_time"] = war_start_time
    
    await save_game(chat_id, game["creator_id"], True, [attacker_id, target_id], war_start_time, game.get("last_war"))
    
    # Отправляем изображение войны
    await send_war_image(chat_id, attacker_country, target_country)
    
    # Объявляем войну
    war_message = await callback.message.answer(
        f"⚔️ ВОЙНА НАЧАЛАСЬ! ⚔️\n\n"
        f"{attacker_country.emoji} {attacker.username} атакует {target_country.emoji} {target.username}!\n"
        f"Битва продлится 30 секунд...\n\n"
        f"Атакующий: ⚔️{attacker.army_level} 💰{int(attacker.money)}\n"
        f"Защитник: ⚔️{target.army_level} 💰{int(target.money)}"
    )
    
    # Запускаем отсчет времени
    await asyncio.sleep(30)
    
    # Завершаем войну
    await finish_war(chat_id, attacker, target, war_message)

async def finish_war(chat_id: int, attacker: Player, target: Player, war_message: Message):
    """Завершить войну"""
    # Перезагружаем данные игроков (на случай, если они обновились)
    attacker = await load_player(attacker.user_id, chat_id)
    target = await load_player(target.user_id, chat_id)
    
    if not attacker or not target:
        print(f"❌ Ошибка при завершении войны: игроки не найдены")
        return
    
    attacker_country = COUNTRIES.get(attacker.country)
    target_country = COUNTRIES.get(target.country)
    
    if not attacker_country or not target_country:
        print(f"❌ Ошибка при завершении войны: страны не найдены")
        return
    
    # Рассчитываем шансы на победу
    attacker_power = attacker.army_level * (1 + attacker.money / 10000)
    target_power = target.army_level * (1 + target.money / 10000)
    
    total_power = attacker_power + target_power
    attacker_win_chance = attacker_power / total_power
    
    # Определяем победителя
    if random.random() < attacker_win_chance:
        winner = attacker
        loser = target
        winner_country = attacker_country
        loser_country = target_country
    else:
        winner = target
        loser = attacker
        winner_country = target_country
        loser_country = attacker_country
    
    # Рассчитываем трофеи (10% от денег проигравшего)
    trophy = int(loser.money * 0.1)
    
    # Обновляем статистику
    winner.wins += 1
    loser.losses += 1
    
    # Передаем трофеи
    winner.money += trophy
    loser.money -= trophy
    
    # Гарантируем, что у проигравшего останется минимум 100 монет
    if loser.money < 100:
        loser.money = 100
    
    # Сохраняем изменения
    await save_player(winner, chat_id)
    await save_player(loser, chat_id)
    
    # Обновляем данные игры
    game = await load_game(chat_id)
    if game:
        game["war_active"] = False
        game["war_participants"] = []
        game["last_war"] = datetime.now()
        await save_game(chat_id, game["creator_id"], False, [], None, game["last_war"])
    
    # Отправляем результат
    result_text = (
        f"🏁 ВОЙНА ЗАВЕРШЕНА! 🏁\n\n"
        f"🏆 ПОБЕДИТЕЛЬ: {winner_country.emoji} {winner.username}\n"
        f"💀 ПРОИГРАВШИЙ: {loser_country.emoji} {loser.username}\n\n"
        f"💰 Трофеи: {trophy} монет\n"
        f"📊 Шансы на победу: {attacker_win_chance*100:.1f}% vs {100 - attacker_win_chance*100:.1f}%\n\n"
        f"🎖️ {winner.username}: {winner.wins} побед / {winner.losses} поражений\n"
        f"🎖️ {loser.username}: {loser.wins} побед / {loser.losses} поражений"
    )
    
    await war_message.edit_text(result_text)
    
    # Отправляем уведомление о возможности новой войны
    await asyncio.sleep(2)
    await war_message.answer("⚔️ Новая война будет возможна через 1 минуту.")

async def handle_transfer_money(callback: CallbackQuery):
    """Обработка передачи денег"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"💸 Передача денег запрошена пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя передавать деньги!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед передачей
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При передаче денег начислен доход: {income:.2f} монет")
    
    # Показываем выбор игрока
    builder = await get_players_keyboard(chat_id, user_id, "transmoney")
    text = "💸 Выберите игрока для передачи денег:"
    
    if income > 0:
        text = f"💰 Вы получили {income:.2f} монет!\n\n" + text
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_transfer_army(callback: CallbackQuery):
    """Обработка передачи армии"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"🎖️ Передача армии запрошена пользователем {user_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя передавать армию!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед передачей
    income = await update_player_income_in_db(user_id, chat_id)
    print(f"💰 При передаче армии начислен доход: {income:.2f} монет")
    
    # Показываем выбор игрока
    builder = await get_players_keyboard(chat_id, user_id, "transarmy")
    text = "🎖️ Выберите игрока для передачи армии:"
    
    if income > 0:
        text = f"💰 Вы получили {income:.2f} монет!\n\n" + text
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_transfer_confirmation(callback: CallbackQuery):
    """Обработка подтверждения передачи"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    transfer_type = data[0]  # transmoney или transarmy
    target_id = int(data[1])
    user_id = callback.from_user.id
    
    if user_id == target_id:
        await callback.answer("❌ Нельзя передавать самому себе!")
        return
    
    print(f"✅ Подтверждение передачи {transfer_type} от {user_id} к {target_id}")
    
    chat_id, game = await find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Игра не найдена!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя передавать ресурсы!")
        return
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед передачей
    print(f"💰 Обновляем доход для отправителя {user_id}")
    await update_player_income_in_db(user_id, chat_id)
    
    print(f"💰 Обновляем доход для получателя {target_id}")
    await update_player_income_in_db(target_id, chat_id)
    
    # Загружаем игроков
    sender = await load_player(user_id, chat_id)
    receiver = await load_player(target_id, chat_id)
    
    if not sender or not receiver:
        await callback.answer("❌ Игрок не найден!")
        return
    
    # Сохраняем данные перевода для последующего использования
    transfer_data.transfers[user_id] = (target_id, transfer_type, chat_id)
    
    if transfer_type == "transmoney":
        max_amount = int(sender.money)
        await callback.message.edit_text(
            f"💸 Вы передаете деньги игроку {receiver.username}\n\n"
            f"💰 Ваш баланс: {max_amount}\n"
            f"Введите сумму для передачи (макс. {max_amount}):"
        )
    else:  # transarmy
        max_army = sender.army_level - 1  # Минимум 1 уровень армии должен остаться
        if max_army <= 0:
            await callback.answer("❌ У вас минимальный уровень армии!")
            return
        
        await callback.message.edit_text(
            f"🎖️ Вы передаете армию игроку {receiver.username}\n\n"
            f"⚔️ Ваш уровень армии: {sender.army_level}\n"
            f"Введите количество уровней для передачи (макс. {max_army}):"
        )
    
    await callback.answer()

async def handle_transfer_amount(message: Message):
    """Обработка ввода суммы перевода"""
    user_id = message.from_user.id
    
    if user_id not in transfer_data.transfers:
        return
    
    target_id, transfer_type, chat_id = transfer_data.transfers[user_id]
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной!")
            return
    except ValueError:
        await message.answer("❌ Введите число!")
        return
    
    # Удаляем данные перевода
    del transfer_data.transfers[user_id]
    
    # ПРИНУДИТЕЛЬНО обновляем доход перед передачей
    await update_player_income_in_db(user_id, chat_id)
    await update_player_income_in_db(target_id, chat_id)
    
    # Загружаем игроков
    sender = await load_player(user_id, chat_id)
    receiver = await load_player(target_id, chat_id)
    
    if not sender or not receiver:
        await message.answer("❌ Ошибка загрузки данных!")
        return
    
    if transfer_type == "transmoney":
        max_amount = int(sender.money)
        if amount > max_amount:
            await message.answer(f"❌ У вас недостаточно денег! Максимум: {max_amount}")
            return
        
        # Выполняем перевод
        sender.money -= amount
        receiver.money += amount
        
        await save_player(sender, chat_id)
        await save_player(receiver, chat_id)
        
        await message.answer(
            f"✅ Вы передали {amount}💰 игроку {receiver.username}\n"
            f"💰 Ваш новый баланс: {int(sender.money)}"
        )
        
        # Уведомляем получателя
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🎁 Вы получили {amount}💰 от {sender.username}!"
            )
        except:
            pass
        
    else:  # transarmy
        max_army = sender.army_level - 1
        if amount > max_army:
            await message.answer(f"❌ Нельзя передать столько уровней! Максимум: {max_army}")
            return
        
        # Выполняем перевод армии
        sender.army_level -= amount
        receiver.army_level += amount
        
        await save_player(sender, chat_id)
        await save_player(receiver, chat_id)
        
        await message.answer(
            f"✅ Вы передали {amount} уровней армии игроку {receiver.username}\n"
            f"⚔️ Ваш новый уровень: {sender.army_level}"
        )
        
        # Уведомляем получателя
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🎁 Вы получили {amount} уровней армии от {sender.username}! Новый уровень: {receiver.army_level}"
            )
        except:
            pass
    
    # Обновляем меню отправителя
    await show_player_menu(message, sender)

async def handle_cancel(callback: CallbackQuery):
    """Обработка отмены действия"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    user_id = callback.from_user.id
    
    if target_player_id != user_id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    print(f"❌ Отмена действия пользователем {user_id}")
    
    # Удаляем данные перевода, если они есть
    if user_id in transfer_data.transfers:
        del transfer_data.transfers[user_id]
    
    # Возвращаемся в главное меню
    await show_player_menu(callback.message)

async def handle_admin_reset(message: Message):
    """Обработка команды сброса игры (только для админов)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для этой команды!")
        return
    
    chat_id = message.chat.id
    
    # Удаляем игру
    await delete_game(chat_id)
    
    await message.answer("✅ Игра полностью сброшена! Для начала новой игры используйте /game")

async def handle_admin_income(message: Message):
    """Обработка команды принудительного обновления дохода (только для админов)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для этой команды!")
        return
    
    await force_update_all_incomes()
    await message.answer("✅ Доход обновлен для всех игроков!")

async def handle_admin_debug(message: Message):
    """Команда для отладки конкретного пользователя"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет прав для этой команды!")
        return
    
    try:
        # Извлекаем user_id из команды /debug_123456
        command = message.text.split()
        if len(command) != 2:
            await message.answer("❌ Использование: /debug USER_ID")
            return
        
        debug_user_id = int(command[1])
        
        # Находим игру пользователя
        chat_id, game = await find_player_game(debug_user_id)
        
        if not chat_id or not game:
            await message.answer(f"❌ Пользователь {debug_user_id} не найден в игре")
            return
        
        # Загружаем игрока
        player = await load_player(debug_user_id, chat_id)
        if not player:
            await message.answer(f"❌ Не удалось загрузить данные игрока {debug_user_id}")
            return
        
        # Обновляем доход
        income = await update_player_income_in_db(debug_user_id, chat_id)
        
        # Загружаем обновленного игрока
        player = await load_player(debug_user_id, chat_id)
        
        # Отправляем отладочную информацию
        country = COUNTRIES.get(player.country, Country("Неизвестно", "❓", 0))
        
        debug_text = (
            f"🔍 ОТЛАДКА ИГРОКА {player.username} (ID: {debug_user_id})\n\n"
            f"🌍 Страна: {country.emoji} {player.country}\n"
            f"💰 Деньги: {player.money:.2f}\n"
            f"⚔️ Уровень армии: {player.army_level}\n"
            f"🏙️ Уровень города: {player.city_level}\n"
            f"⏰ Последний доход: {player.last_income}\n"
            f"🕒 Текущее время: {datetime.now()}\n"
            f"⏱️ Разница: {(datetime.now() - player.last_income).total_seconds():.1f} сек\n"
            f"📈 Пассивный доход: {country.base_income * player.city_level:.1f}/сек\n"
            f"💸 Начислено сейчас: {income:.2f} монет\n"
            f"🎮 Чат игры: {chat_id}\n"
            f"⚔️ Война активна: {'Да' if game['war_active'] else 'Нет'}"
        )
        
        await message.answer(debug_text)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка отладки: {e}")

# ========== ФОНОВАЯ ЗАДАЧА ОБНОВЛЕНИЯ ДОХОДА ==========

async def income_background_task():
    """Фоновая задача для обновления дохода"""
    while True:
        try:
            print("🔄 Запуск фонового обновления дохода...")
            
            # Обновляем доход для всех игроков во всех чатах
            games = await get_all_games()
            
            print(f"📊 Найдено {len(games)} активных игр")
            
            for chat_id, game in games.items():
                if not game["war_active"]:  # Если нет активной войны
                    print(f"   Обновляем чат {chat_id}")
                    await update_all_players_income_in_chat(chat_id)
            
            print("✅ Фоновое обновление завершено")
            
            # Ждем 5 секунд перед следующим обновлением
            await asyncio.sleep(5)
            
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче обновления дохода: {e}")
            await asyncio.sleep(10)

# ========== ЗАПУСК БОТА ==========

async def main():
    global bot
    
    # Инициализация базы данных
    init_database()
    
    # Инициализация бота
    bot = Bot(token=TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация обработчиков команд
    dp.message.register(handle_start, Command("start"))
    dp.message.register(handle_game, Command("game"))
    dp.message.register(handle_join, Command("join"))
    dp.message.register(handle_admin_reset, Command("reset"))
    dp.message.register(handle_admin_income, Command("update_income"))
    dp.message.register(handle_admin_debug, Command("debug"))
    dp.message.register(handle_transfer_amount, F.text.regexp(r'^\d+$'))
    
    # Регистрация обработчиков callback-запросов
    dp.callback_query.register(handle_country_selection, F.data.startswith("country_"))
    dp.callback_query.register(handle_stats, F.data.startswith("stats_"))
    dp.callback_query.register(handle_upgrade_army, F.data.startswith("upgrade_army_"))
    dp.callback_query.register(handle_upgrade_city, F.data.startswith("upgrade_city_"))
    dp.callback_query.register(handle_top, F.data.startswith("top_"))
    dp.callback_query.register(handle_refresh, F.data.startswith("refresh_"))
    dp.callback_query.register(handle_change_country, F.data.startswith("change_country_"))
    dp.callback_query.register(handle_start_war, F.data.startswith("start_war_"))
    dp.callback_query.register(handle_war_target, F.data.startswith("wartarget_"))
    dp.callback_query.register(handle_transfer_money, F.data.startswith("transfer_money_"))
    dp.callback_query.register(handle_transfer_army, F.data.startswith("transfer_army_"))
    dp.callback_query.register(handle_transfer_confirmation, F.data.startswith("transmoney_") | F.data.startswith("transarmy_"))
    dp.callback_query.register(handle_cancel, F.data.startswith("cancel_"))
    
    # Запуск фоновой задачи обновления дохода
    asyncio.create_task(income_background_task())
    
    print("=" * 50)
    print("✅ Бот запущен и готов к работе!")
    print(f"👑 Админ ID: {ADMIN_ID}")
    print(f"📁 Папка для изображений войны: {WAR_IMAGES_FOLDER}")
    print(f"💾 База данных: {DATABASE_FILE}")
    print("💰 Система пассивного дохода активна (обновление каждые 5 секунд)")
    print("🔄 Кнопка 'Обновить деньги' теперь работает правильно!")
    print("🔍 Для отладки используйте команду /debug USER_ID")
    print("=" * 50)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
