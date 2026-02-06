import asyncio
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, ChatMemberAdministrator, InputFile
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Конфигурация
TOKEN = "8022954037:AAHH75JVSpIBXGfmgV3PCZcR2h85Y5qSI5A"
ADMIN_ID = 123456789  # Ваш ID для админки

# Настройки базы данных
DATABASE_FILE = "game_database.db"
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

# ========== БАЗА ДАННЫХ ==========

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

def save_game(chat_id: int, creator_id: int, war_active: bool = False, 
              war_participants: List[int] = None, war_start_time: Optional[datetime] = None,
              last_war: Optional[datetime] = None):
    """Сохранить или обновить игру"""
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

def save_player(player: Player, chat_id: int):
    """Сохранить или обновить игрока"""
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

def load_game(chat_id: int) -> Optional[Dict]:
    """Загрузить игру по chat_id"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM games WHERE chat_id = ?', (chat_id,))
    game_data = cursor.fetchone()
    
    if not game_data:
        conn.close()
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
    
    conn.close()
    return game

def load_player(user_id: int, chat_id: int) -> Optional[Player]:
    """Загрузить игрока по user_id и chat_id"""
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

def load_all_players(chat_id: int) -> Dict[int, Player]:
    """Загрузить всех игроков в игре"""
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

def get_game_players_count(chat_id: int) -> int:
    """Получить количество игроков в игре"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM players WHERE chat_id = ?', (chat_id,))
    count = cursor.fetchone()[0]
    conn.close()
    
    return count

def delete_game(chat_id: int):
    """Удалить игру и всех игроков"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM players WHERE chat_id = ?', (chat_id,))
    cursor.execute('DELETE FROM games WHERE chat_id = ?', (chat_id,))
    
    conn.commit()
    conn.close()

def find_player_game(user_id: int) -> Tuple[Optional[int], Optional[Dict]]:
    """Найти игру, в которой находится игрок"""
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

def get_all_games() -> Dict[int, Dict]:
    """Получить все активные игры"""
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

async def update_income():
    """Обновление пассивного дохода"""
    while True:
        await asyncio.sleep(1)  # Проверяем каждую секунду
        current_time = datetime.now()
        
        # Получаем все активные игры
        games = get_all_games()
        
        for chat_id, game in games.items():
            if game["war_active"]:
                continue  # Пропускаем игры с активной войной
            
            # Загружаем всех игроков в этой игре
            players = load_all_players(chat_id)
            
            for player in players.values():
                # Вычисляем разницу во времени
                time_diff = (current_time - player.last_income).total_seconds()
                
                if time_diff > 0:  # Если прошло больше 0 секунд
                    country = COUNTRIES.get(player.country)
                    if country:
                        # Рассчитываем доход
                        income = country.base_income * player.city_level * time_diff
                        
                        # Обновляем деньги игрока
                        player.money += income
                        player.last_income = current_time
                        
                        # Сохраняем обновленного игрока в базу данных
                        save_player(player, chat_id)
                        
                        # Логирование для отладки
                        # print(f"💰 Игрок {player.username} получил {income:.2f} монет")

async def update_player_income_in_db(user_id: int, chat_id: int):
    """Обновить доход конкретного игрока"""
    player = load_player(user_id, chat_id)
    if not player:
        return
    
    current_time = datetime.now()
    time_diff = (current_time - player.last_income).total_seconds()
    
    if time_diff > 0:
        country = COUNTRIES.get(player.country)
        if country:
            income = country.base_income * player.city_level * time_diff
            player.money += income
            player.last_income = current_time
            save_player(player, chat_id)
            return income
    return 0

def get_game_keyboard(player_id: int) -> InlineKeyboardBuilder:
    """Клавиатура для игрока"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Статистика", callback_data=f"stats_{player_id}")
    builder.button(text="⚔️ Улучшить армию", callback_data=f"upgrade_army_{player_id}")
    builder.button(text="🏙️ Улучшить город", callback_data=f"upgrade_city_{player_id}")
    builder.button(text="🌍 Топ игроков", callback_data=f"top_{player_id}")
    builder.button(text="⚔️ Начать войну", callback_data=f"start_war_{player_id}")
    builder.button(text="🔄 Обновить", callback_data=f"refresh_{player_id}")
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

def get_players_keyboard(chat_id: int, exclude_id: int, action: str) -> InlineKeyboardBuilder:
    """Клавиатура выбора игрока для передачи"""
    builder = InlineKeyboardBuilder()
    players = load_all_players(chat_id)
    
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

def get_war_targets_keyboard(chat_id: int, attacker_id: int) -> InlineKeyboardBuilder:
    """Клавиатура выбора цели для войны"""
    builder = InlineKeyboardBuilder()
    players = load_all_players(chat_id)
    
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
    
    # Проверяем, есть ли уже игра
    existing_game = load_game(chat_id)
    
    if existing_game and existing_game["war_active"]:
        await message.answer("⚔️ Сейчас идет война! Подождите ее окончания.")
        return
    
    if not existing_game:
        # Создание новой игры
        save_game(chat_id, message.from_user.id)
        await message.answer("🎮 Игра создана! Чтобы присоединиться, нажмите /join")
    else:
        # Проверка, участвует ли уже пользователь
        player = load_player(message.from_user.id, chat_id)
        if player:
            # Обновляем доход перед показом меню
            await update_player_income_in_db(player.user_id, chat_id)
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
    
    game = load_game(chat_id)
    if not game:
        await message.answer("❌ Игра не создана! Сначала создайте игру с помощью /game")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await message.answer("⚔️ Сейчас идет война! Подождите ее окончания.")
        return
    
    # Проверка, участвует ли уже пользователь
    player = load_player(user_id, chat_id)
    if player:
        # Обновляем доход перед показом меню
        await update_player_income_in_db(player.user_id, chat_id)
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
    
    # Проверяем, есть ли игра в этом чате
    game = load_game(chat_id)
    if not game:
        # Ищем игру, где есть пользователь
        found_chat_id, found_game = find_player_game(user_id)
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
    players = load_all_players(chat_id)
    for player in players.values():
        if player.country == country_id and player.user_id != user_id:
            await callback.answer("❌ Эта страна уже занята!")
            return
    
    # Создание игрока или обновление страны
    existing_player = load_player(user_id, chat_id)
    
    if existing_player:
        # Смена страны существующего игрока
        existing_player.country = country_id
        save_player(existing_player, chat_id)
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
        save_player(player, chat_id)
        action_text = "присоединились к игре как"
    
    country = COUNTRIES[country_id]
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
    chat_id, game = find_player_game(player.user_id)
    if not game or not chat_id:
        return
    
    # Обновляем доход перед показом меню
    income = await update_player_income_in_db(player.user_id, chat_id)
    
    # Загружаем обновленного игрока
    updated_player = load_player(player.user_id, chat_id)
    if not updated_player:
        return
    
    country = COUNTRIES.get(updated_player.country)
    if not country:
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
    except TelegramBadRequest:
        # Если сообщение нельзя редактировать, отправляем новое
        await message.answer(text, reply_markup=builder.as_markup())

async def show_player_menu(message: Message, player: Optional[Player] = None):
    """Показать меню игрока"""
    user_id = message.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await message.answer("❌ Вы не в игре! Используйте /join")
        return
    
    if not player:
        player = load_player(user_id, chat_id)
        if not player:
            await message.answer("❌ Вы не в игре! Используйте /join")
            return
    
    # Обновляем доход перед показом меню
    await update_player_income_in_db(user_id, chat_id)
    
    # Загружаем обновленного игрока
    updated_player = load_player(user_id, chat_id)
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
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед показом статистики
    await update_player_income_in_db(user_id, chat_id)
    
    player = load_player(user_id, chat_id)
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
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед улучшением
    await update_player_income_in_db(user_id, chat_id)
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя улучшать армию!")
        return
    
    player = load_player(user_id, chat_id)
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
        save_player(player, chat_id)
        
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
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед улучшением
    await update_player_income_in_db(user_id, chat_id)
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя улучшать город!")
        return
    
    player = load_player(user_id, chat_id)
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
        save_player(player, chat_id)
        
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
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Игра не найдена!")
        return
    
    # Загружаем всех игроков
    players = load_all_players(chat_id)
    
    # Сортировка игроков по деньгам
    sorted_players = sorted(
        players.values(),
        key=lambda p: p.money,
        reverse=True
    )
    
    text = "🏆 Топ игроков:\n\n"
    for i, player in enumerate(sorted_players[:10], 1):
        country = COUNTRIES.get(player.country, COUNTRIES["russia"])
        text += f"{i}. {country.emoji} {player.username}: {int(player.money)}💰 (⚔{player.army_level} 🏙{player.city_level})\n"
    
    await callback.message.edit_text(text)
    await callback.answer()

async def handle_change_country(callback: CallbackQuery):
    """Обработка смены страны"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя менять страну!")
        return
    
    player = load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Стоимость смены страны - 10% от текущих денег
    change_cost = int(player.money * 0.1)
    
    if player.money < change_cost:
        await callback.answer(f"❌ Для смены страны нужно {change_cost}💰!")
        return
    
    builder = get_countries_keyboard()
    builder.button(text="❌ Отмена", callback_data=f"cancel_{user_id}")
    builder.adjust(2, 1)
    
    await callback.message.edit_text(
        f"🌍 Выберите новую страну:\n"
        f"💸 Стоимость смены: {change_cost}💰",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_transfer_money(callback: CallbackQuery):
    """Обработка передачи денег"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед переводом
    await update_player_income_in_db(user_id, chat_id)
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя передавать деньги!")
        return
    
    # Проверка, что есть другие игроки
    players_count = get_game_players_count(chat_id)
    if players_count < 2:
        await callback.answer("❌ Нет других игроков для передачи!")
        return
    
    builder = get_players_keyboard(chat_id, user_id, "transferto")
    await callback.message.edit_text(
        "👤 Выберите игрока, которому хотите передать деньги:",
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
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед переводом
    await update_player_income_in_db(user_id, chat_id)
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Во время войны нельзя передавать армию!")
        return
    
    # Проверка, что есть другие игроки
    players_count = get_game_players_count(chat_id)
    if players_count < 2:
        await callback.answer("❌ Нет других игроков для передачи!")
        return
    
    builder = get_players_keyboard(chat_id, user_id, "transferarmyto")
    await callback.message.edit_text(
        "👤 Выберите игрока, которому хотите передать армию:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_transfer_to_selection(callback: CallbackQuery):
    """Обработка выбора игрока для передачи"""
    user_id = callback.from_user.id
    
    # Определяем тип перевода из callback данных
    callback_data = callback.data
    if callback_data.startswith("transferto_"):
        transfer_type = "money"
        target_id = int(callback_data.split('_')[1])
    elif callback_data.startswith("transferarmyto_"):
        transfer_type = "army"
        target_id = int(callback_data.split('_')[1])
    else:
        await callback.answer("❌ Неверный формат данных!")
        return
    
    # Находим игру
    chat_id, game = find_player_game(user_id)
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Проверяем, что целевой игрок существует
    target_player = load_player(target_id, chat_id)
    if not target_player:
        await callback.answer("❌ Игрок не найден!")
        return
    
    if target_id == user_id:
        await callback.answer("❌ Нельзя передать самому себе!")
        return
    
    # Сохраняем данные перевода
    transfer_data.transfers[user_id] = (target_id, transfer_type, chat_id)
    
    player = load_player(user_id, chat_id)
    if not player:
        await callback.answer("❌ Вы не в игре!")
        return
    
    if transfer_type == "money":
        await callback.message.edit_text(
            f"💸 Перевод денег игроку {target_player.username}\n\n"
            f"💰 У вас есть: {int(player.money)} монет\n"
            f"👤 Получатель: {target_player.username}\n\n"
            f"Введите сумму для перевода (комиссия 5%):"
        )
    else:  # army
        country = COUNTRIES.get(player.country)
        if not country:
            await callback.answer("❌ Ошибка данных страны!")
            return
        
        cost_per_level = country.army_cost * player.army_level
        
        await callback.message.edit_text(
            f"🎖️ Перевод армии игроку {target_player.username}\n\n"
            f"⚔️ Ваш уровень армии: {player.army_level}\n"
            f"⚔️ Уровень получателя: {target_player.army_level}\n"
            f"💸 Стоимость 1 уровня: {cost_per_level}💰\n\n"
            f"Введите количество уровней для передачи (максимум {player.army_level - 1}):"
        )
    
    await callback.answer()

async def handle_transfer_amount(message: Message):
    """Обработка ввода суммы для перевода"""
    user_id = message.from_user.id
    
    # Проверяем, есть ли активный перевод
    if user_id not in transfer_data.transfers:
        await message.answer("❌ У вас нет активных переводов!")
        return
    
    # Получаем данные перевода
    target_id, transfer_type, chat_id = transfer_data.transfers[user_id]
    
    # Удаляем данные перевода, чтобы предотвратить повторное использование
    del transfer_data.transfers[user_id]
    
    if not chat_id:
        await message.answer("❌ Игра не найдена!")
        return
    
    player = load_player(user_id, chat_id)
    target_player = load_player(target_id, chat_id)
    
    if not player or not target_player:
        await message.answer("❌ Игрок не найден!")
        return
    
    if transfer_type == "money":
        # Перевод денег
        try:
            amount = float(message.text.replace(',', '.'))
            if amount <= 0:
                await message.answer("❌ Сумма должна быть больше 0!")
                return
            
            if player.money < amount:
                await message.answer(f"❌ У вас недостаточно денег! У вас {int(player.money)}💰")
                return
            
            # Комиссия 5%
            commission = amount * 0.05
            transfer_amount = amount - commission
            
            # Выполняем перевод
            player.money -= amount
            target_player.money += transfer_amount
            
            # Сохраняем изменения
            save_player(player, chat_id)
            save_player(target_player, chat_id)
            
            await message.answer(
                f"✅ Перевод успешно выполнен!\n\n"
                f"📤 Отправитель: {player.username}\n"
                f"📥 Получатель: {target_player.username}\n"
                f"💰 Сумма перевода: {int(transfer_amount)}💰\n"
                f"💸 Комиссия (5%): {int(commission)}💰\n"
                f"💵 Ваш баланс: {int(player.money)}💰"
            )
            
            # Уведомляем получателя, если он в другом чате
            try:
                target_chat_id, _ = find_player_game(target_id)
                if target_chat_id and target_chat_id != message.chat.id:
                    await bot.send_message(
                        target_chat_id,
                        f"💰 Вы получили перевод!\n"
                        f"📤 От: {player.username}\n"
                        f"💸 Сумма: {int(transfer_amount)}💰\n"
                        f"💵 Ваш баланс: {int(target_player.money)}💰"
                    )
            except:
                pass
            
            # Показываем обновленное меню
            await update_player_menu(message, player)
            
        except ValueError:
            await message.answer("❌ Пожалуйста, введите число!")
    
    else:
        # Перевод армии
        try:
            amount = int(message.text)
            if amount <= 0:
                await message.answer("❌ Количество должно быть больше 0!")
                return
            
            if player.army_level <= 1:
                await message.answer("❌ У вас минимальный уровень армии!")
                return
            
            # Максимальное количество уровней для передачи
            max_transfer = player.army_level - 1
            if amount > max_transfer:
                await message.answer(f"❌ Вы можете передать максимум {max_transfer} уровней!")
                return
            
            # Стоимость передачи
            country = COUNTRIES.get(player.country)
            if not country:
                await message.answer("❌ Ошибка данных страны!")
                return
            
            cost_per_level = country.army_cost * player.army_level
            total_cost = cost_per_level * amount
            
            if player.money < total_cost:
                await message.answer(f"❌ У вас недостаточно денег! Нужно {total_cost}💰")
                return
            
            # Выполняем передачу армии
            player.money -= total_cost
            player.army_level -= amount
            target_player.army_level += amount
            
            # Сохраняем изменения
            save_player(player, chat_id)
            save_player(target_player, chat_id)
            
            await message.answer(
                f"✅ Перевод армии успешно выполнен!\n\n"
                f"📤 Отправитель: {player.username}\n"
                f"📥 Получатель: {target_player.username}\n"
                f"🎖️ Уровней передано: {amount}\n"
                f"💸 Стоимость: {total_cost}💰\n"
                f"⚔️ Ваш уровень армии: {player.army_level}\n"
                f"⚔️ Уровень получателя: {target_player.army_level}"
            )
            
            # Уведомляем получателя, если он в другом чате
            try:
                target_chat_id, _ = find_player_game(target_id)
                if target_chat_id and target_chat_id != message.chat.id:
                    await bot.send_message(
                        target_chat_id,
                        f"🎖️ Вы получили армию!\n"
                        f"📤 От: {player.username}\n"
                        f"⚔️ Уровней получено: {amount}\n"
                        f"⚔️ Ваш уровень армии: {target_player.army_level}"
                    )
            except:
                pass
            
            # Показываем обновленное меню
            await update_player_menu(message, player)
            
        except ValueError:
            await message.answer("❌ Пожалуйста, введите целое число!")

async def handle_cancel(callback: CallbackQuery):
    """Обработка отмены действия"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    # Удаляем активный перевод, если есть
    if target_player_id in transfer_data.transfers:
        del transfer_data.transfers[target_player_id]
    
    await show_player_menu(callback.message)
    await callback.answer("❌ Действие отменено")

async def handle_start_war(callback: CallbackQuery):
    """Обработка начала войны"""
    data = callback.data.split('_')
    if len(data) != 3:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[2])
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    # Обновляем доход перед войной
    await update_player_income_in_db(user_id, chat_id)
    
    # Проверка на активную войну
    if game["war_active"]:
        await callback.answer("⚔️ Война уже идет!")
        return
    
    # Проверка кулдауна (минимум 5 минут между войнами)
    if game["last_war"] and (datetime.now() - game["last_war"]).total_seconds() < 300:
        remaining = 300 - (datetime.now() - game["last_war"]).total_seconds()
        await callback.answer(f"⏳ До следующей войны осталось: {int(remaining)} сек")
        return
    
    # Проверка, что есть другие игроки
    players_count = get_game_players_count(chat_id)
    if players_count < 2:
        await callback.answer("❌ Недостаточно игроков для войны!")
        return
    
    builder = get_war_targets_keyboard(chat_id, user_id)
    await callback.message.edit_text(
        "🎯 Выберите противника для войны:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

async def handle_war_target(callback: CallbackQuery):
    """Обработка выбора цели для войны"""
    user_id = callback.from_user.id
    chat_id, game = find_player_game(user_id)
    
    if not game or not chat_id:
        await callback.answer("❌ Вы не в игре!")
        return
    
    target_id = int(callback.data.split('_')[1])
    
    if target_id == user_id:
        await callback.answer("❌ Нельзя воевать с самим собой!")
        return
    
    target_player = load_player(target_id, chat_id)
    if not target_player:
        await callback.answer("❌ Игрок не найден!")
        return
    
    # Обновляем доход игроков перед войной
    await update_player_income_in_db(user_id, chat_id)
    await update_player_income_in_db(target_id, chat_id)
    
    # Начало войны
    game["war_active"] = True
    game["war_participants"] = [user_id, target_id]
    game["war_start_time"] = datetime.now()
    
    # Сохраняем обновленную игру
    save_game(
        chat_id=chat_id,
        creator_id=game["creator_id"],
        war_active=True,
        war_participants=game["war_participants"],
        war_start_time=game["war_start_time"],
        last_war=game["last_war"]
    )
    
    attacker = load_player(user_id, chat_id)
    attacker_country = COUNTRIES.get(attacker.country) if attacker else None
    target_country = COUNTRIES.get(target_player.country) if target_player else None
    
    if not attacker or not attacker_country or not target_country:
        await callback.answer("❌ Ошибка данных!")
        return
    
    war_message = (
        f"⚔️ ⚔️ ⚔️ ВОЙНА НАЧАЛАСЬ! ⚔️ ⚔️ ⚔️\n\n"
        f"{attacker_country.emoji} {attacker.username} объявил войну {target_country.emoji} {target_player.username}!\n\n"
        f"Бой будет длиться 60 секунд. Победит тот, у кого выше уровень армии!"
    )
    
    await callback.message.edit_text(war_message)
    
    # Отправляем изображение войны
    await send_war_image(chat_id, attacker_country, target_country)
    
    # Запуск таймера войны
    asyncio.create_task(war_countdown(chat_id))

async def war_countdown(chat_id: int):
    """Таймер войны"""
    await asyncio.sleep(60)  # Война длится 60 секунд
    
    game = load_game(chat_id)
    if not game:
        return
    
    if not game["war_active"] or len(game["war_participants"]) != 2:
        # Сбрасываем состояние войны
        game["war_active"] = False
        game["war_participants"] = []
        game["war_start_time"] = None
        save_game(
            chat_id=chat_id,
            creator_id=game["creator_id"],
            war_active=False,
            war_participants=[],
            war_start_time=None,
            last_war=game["last_war"]
        )
        return
    
    # Определение победителя
    attacker_id = game["war_participants"][0]
    target_id = game["war_participants"][1]
    
    attacker = load_player(attacker_id, chat_id)
    target = load_player(target_id, chat_id)
    
    if not attacker or not target:
        # Сбрасываем состояние войны
        game["war_active"] = False
        game["war_participants"] = []
        game["war_start_time"] = None
        save_game(
            chat_id=chat_id,
            creator_id=game["creator_id"],
            war_active=False,
            war_participants=[],
            war_start_time=None,
            last_war=game["last_war"]
        )
        return
    
    attacker_power = attacker.army_level * (1 + 0.1 * attacker.city_level)
    target_power = target.army_level * (1 + 0.1 * target.city_level)
    
    # Добавление случайности
    attacker_power *= random.uniform(0.9, 1.1)
    target_power *= random.uniform(0.9, 1.1)
    
    if attacker_power > target_power:
        winner = attacker
        loser = target
        winner.wins += 1
        loser.losses += 1
        
        # Награда победителю
        loot = loser.money * 0.1  # 10% денег проигравшего
        winner.money += loot
        loser.money -= loot
        
        result_message = (
            f"🎉 ВОЙНА ОКОНЧЕНА! 🎉\n\n"
            f"🏆 ПОБЕДИТЕЛЬ: {COUNTRIES.get(winner.country, COUNTRIES['russia']).emoji} {winner.username}\n"
            f"💀 ПРОИГРАВШИЙ: {COUNTRIES.get(loser.country, COUNTRIES['russia']).emoji} {loser.username}\n\n"
            f"⚔️ Сила атаки: {attacker_power:.1f} vs {target_power:.1f}\n"
            f"💰 Добыча: {int(loot)} монет"
        )
    else:
        winner = target
        loser = attacker
        winner.wins += 1
        loser.losses += 1
        
        loot = loser.money * 0.1
        winner.money += loot
        loser.money -= loot
        
        result_message = (
            f"🎉 ВОЙНА ОКОНЧЕНА! 🎉\n\n"
            f"🏆 ПОБЕДИТЕЛЬ: {COUNTRIES.get(winner.country, COUNTRIES['russia']).emoji} {winner.username}\n"
            f"💀 ПРОИГРАВШИЙ: {COUNTRIES.get(loser.country, COUNTRIES['russia']).emoji} {loser.username}\n\n"
            f"⚔️ Сила атаки: {attacker_power:.1f} vs {target_power:.1f}\n"
            f"💰 Добыча: {int(loot)} монет"
        )
    
    # Сохраняем изменения игроков
    save_player(winner, chat_id)
    save_player(loser, chat_id)
    
    # Сброс состояния войны
    game["war_active"] = False
    game["war_participants"] = []
    game["war_start_time"] = None
    game["last_war"] = datetime.now()
    
    # Сохраняем обновленную игру
    save_game(
        chat_id=chat_id,
        creator_id=game["creator_id"],
        war_active=False,
        war_participants=[],
        war_start_time=None,
        last_war=game["last_war"]
    )
    
    # Отправка результата
    await bot.send_message(chat_id, result_message)

async def handle_refresh(callback: CallbackQuery):
    """Обработка обновления"""
    data = callback.data.split('_')
    if len(data) != 2:
        await callback.answer("❌ Ошибка!")
        return
    
    target_player_id = int(data[1])
    
    if target_player_id != callback.from_user.id:
        await callback.answer("❌ Это не ваша кнопка!")
        return
    
    await show_player_menu(callback.message)
    await callback.answer("🔄 Обновлено!")

async def handle_reset(message: Message):
    """Обработка команды /reset (только для админов)"""
    if message.chat.type == "private":
        await message.answer("🎮 Игра доступна только в групповых чатах!")
        return
    
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Проверка прав администратора
    if not await is_admin_in_chat(chat_id, user_id):
        await message.answer("❌ Только администраторы могут сбрасывать игру!")
        return
    
    delete_game(chat_id)
    await message.answer("✅ Игра сброшена администратором!")

async def main():
    """Основная функция"""
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
    dp.message.register(handle_reset, Command("reset"))
    dp.message.register(handle_transfer_amount)
    
    # Регистрация обработчиков callback-запросов
    dp.callback_query.register(handle_country_selection, F.data.startswith("country_"))
    dp.callback_query.register(handle_stats, F.data.startswith("stats_"))
    dp.callback_query.register(handle_upgrade_army, F.data.startswith("upgrade_army_"))
    dp.callback_query.register(handle_upgrade_city, F.data.startswith("upgrade_city_"))
    dp.callback_query.register(handle_top, F.data.startswith("top_"))
    dp.callback_query.register(handle_start_war, F.data.startswith("start_war_"))
    dp.callback_query.register(handle_war_target, F.data.startswith("wartarget_"))
    dp.callback_query.register(handle_refresh, F.data.startswith("refresh_"))
    dp.callback_query.register(handle_change_country, F.data.startswith("change_country_"))
    dp.callback_query.register(handle_transfer_money, F.data.startswith("transfer_money_"))
    dp.callback_query.register(handle_transfer_army, F.data.startswith("transfer_army_"))
    dp.callback_query.register(handle_transfer_to_selection, F.data.startswith("transferto_"))
    dp.callback_query.register(handle_transfer_to_selection, F.data.startswith("transferarmyto_"))
    dp.callback_query.register(handle_cancel, F.data.startswith("cancel_"))
    
    # Запуск обновления дохода
    asyncio.create_task(update_income())
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
