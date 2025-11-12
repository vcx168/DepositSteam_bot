import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import requests
import os
from dotenv import load_dotenv
import sqlite3
from contextlib import contextmanager

# Загружаем переменные окружения из .env
load_dotenv()

# Получаем токены и URL из .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PLAYWALLET_API_TOKEN = os.getenv("PLAYWALLET_API_TOKEN")
# Получаем URL из .env, с резервным значением
PLAYWALLET_BASE_URL = os.getenv("PLAYWALLET_BASE_URL", "https://api.playwallet.bot")

if not TELEGRAM_BOT_TOKEN or not PLAYWALLET_API_TOKEN:
    raise ValueError("Необходимо указать TELEGRAM_BOT_TOKEN и PLAYWALLET_API_TOKEN в файле .env")

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---

DATABASE_NAME = "bot_database.db"

# --- Структура таблиц ---

# Таблица пользователей
CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_admin BOOLEAN DEFAULT 0,
    steam_wallet_balance REAL DEFAULT 0.0 -- Баланс в "условных" валютах Steam, например, рубли
);
"""

# Таблица транзакций
CREATE_TRANSACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_telegram_id INTEGER NOT NULL,
    type TEXT NOT NULL, -- 'deposit', 'withdrawal', 'steam_purchase', 'bonus' и т.д.
    amount REAL NOT NULL,
    currency TEXT NOT NULL, -- 'TON', 'RUB', 'USD' и т.д.
    status TEXT DEFAULT 'pending', -- 'pending', 'completed', 'failed'
    external_id TEXT, -- ID транзакции в PlayWallet или Steam
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_telegram_id) REFERENCES users (telegram_id)
);
"""

# --- Вспомогательные функции БД ---

@contextmanager
def get_db_connection():
    """Контекстный менеджер для подключения к БД."""
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row # Позволяет обращаться к колонкам по имени
    try:
        yield conn
    except Exception as e:
        logging.error(f"Ошибка работы с БД: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Инициализирует базу данных и создает таблицы."""
    with get_db_connection() as conn:
        conn.execute(CREATE_USERS_TABLE)
        conn.execute(CREATE_TRANSACTIONS_TABLE)
        conn.commit()
        logging.info("База данных инициализирована.")

# --- Функции для работы с пользователями ---

def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Получает пользователя из БД или создает нового."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (telegram_id, username, first_name, last_name)
        )
        cursor.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        user = cursor.fetchone()
        conn.commit()
        return user

def get_user_by_telegram_id(telegram_id: int):
    """Получает пользователя по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )
        return cursor.fetchone()

def set_user_admin_status(telegram_id: int, is_admin: bool):
    """Устанавливает статус администратора для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_admin = ? WHERE telegram_id = ?",
            (1 if is_admin else 0, telegram_id)
        )
        conn.commit()
        logging.info(f"Статус администратора для {telegram_id} изменен на {is_admin}")

def get_all_users():
    """Получает всех пользователей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        return cursor.fetchall()

def update_steam_wallet_balance(telegram_id: int, new_balance: float):
    """Обновляет баланс Steam кошелька пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET steam_wallet_balance = ? WHERE telegram_id = ?",
            (new_balance, telegram_id)
        )
        conn.commit()
        logging.info(f"Баланс Steam кошелька для {telegram_id} обновлен до {new_balance}")

# --- Функции для работы с транзакциями ---

def add_transaction(user_telegram_id: int, type: str, amount: float, currency: str, status: str = 'pending', external_id: str = None, description: str = ""):
    """Добавляет новую транзакцию."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_telegram_id, type, amount, currency, status, external_id, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_telegram_id, type, amount, currency, status, external_id, description)
        )
        transaction_id = cursor.lastrowid
        conn.commit()
        logging.info(f"Добавлена транзакция ID {transaction_id} для пользователя {user_telegram_id}")
        return transaction_id

def get_transactions_by_user(telegram_id: int, limit: int = 10):
    """Получает последние N транзакций пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions WHERE user_telegram_id = ? ORDER BY created_at DESC LIMIT ?",
            (telegram_id, limit)
        )
        return cursor.fetchall()

def get_all_transactions(limit: int = 50):
    """Получает последние N транзакций от всех пользователей (для админ-панели)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return cursor.fetchall()

def update_transaction_status(transaction_id: int, new_status: str):
    """Обновляет статус транзакции."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE transactions SET status = ? WHERE id = ?",
            (new_status, transaction_id)
        )
        conn.commit()
        logging.info(f"Статус транзакции ID {transaction_id} обновлен на {new_status}")

# --- Функции для статистики (для админ-панели) ---

def get_total_users_count():
    """Получает общее количество пользователей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM users")
        return cursor.fetchone()['count']

def get_total_transactions_count():
    """Получает общее количество транзакций."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM transactions")
        return cursor.fetchone()['count']

def get_total_completed_deposit_amount():
    """Получает общую сумму завершённых депозитов."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) as total FROM transactions WHERE type = 'deposit' AND status = 'completed'")
        result = cursor.fetchone()['total']
        return result if result is not None else 0.0

def get_total_completed_withdrawal_amount():
    """Получает общую сумму завершённых выводов."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(amount) as total FROM transactions WHERE type = 'withdrawal' AND status = 'completed'")
        result = cursor.fetchone()['total']
        return result if result is not None else 0.0

def get_recent_transactions(limit: int = 10):
    """Получает последние N транзакций."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,))
        return cursor.fetchall()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

# Вспомогательная функция для отправки запросов к API PlayWallet
def call_playwallet_api(method, data=None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PLAYWALLET_API_TOKEN}"
    }
    url = f"{PLAYWALLET_BASE_URL}/{method}"
    logging.info(f"Отправка запроса к {url}")

    # Логируем данные запроса (если есть)
    if data:
        logging.info(f"Данные запроса: {data}")

    try:
        if data:
            response = requests.post(url, json=data, headers=headers)
        else:
            response = requests.get(url, headers=headers)

        # Логируем статус ответа и текст
        logging.info(f"Статус ответа: {response.status_code}")
        logging.info(f"Текст ответа: {response.text}")

        response.raise_for_status()  # Возбуждает исключение для кодов ошибок HTTP
        return response.json()

    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при запросе к PlayWallet API ({url}): {e}")
        if 'response' in locals():  # Проверяем, существует ли переменная response
            logging.error(f"Ответ API (текст): {response.text}")
            logging.error(f"Ответ API (статус): {response.status_code}")
        return None

# --- АДМИН-ПАНЕЛЬ ---

# Список администраторов (их telegram_id). Заполняется вручную или через команду.
# Пока что для примера, можно сделать команду /add_admin <id>
ADMIN_IDS = [1848571732, 741974404] # ЗАМЕНИТЕ НА СВОЙ РЕАЛЬНЫЙ TELEGRAM ID

def is_admin(telegram_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    user = get_user_by_telegram_id(telegram_id)
    return user and (user['is_admin'] or telegram_id in ADMIN_IDS)

async def send_admin_stats(message: types.Message):
    """Отправляет администратору статистику бота."""
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав для просмотра статистики.")
        return

    total_users = get_total_users_count()
    total_transactions = get_total_transactions_count()
    total_deposits = get_total_completed_deposit_amount()
    total_withdrawals = get_total_completed_withdrawal_amount()
    recent_transactions = get_recent_transactions(limit=5)

    stats_text = (
        "<b>📊 Статистика бота:</b>\n"
        f"Всего пользователей: <code>{total_users}</code>\n"
        f"Всего транзакций: <code>{total_transactions}</code>\n"
        f"Всего пополнений (завершённых): <code>{total_deposits:.2f}</code>\n"
        f"Всего выводов (завершённых): <code>{total_withdrawals:.2f}</code>\n\n"
        "<b>Последние транзакции:</b>\n"
    )
    for tx in recent_transactions:
        stats_text += (
            f"ID: <code>{tx['id']}</code>, "
            f"User: <code>{tx['user_telegram_id']}</code>, "
            f"Тип: <code>{tx['type']}</code>, "
            f"Сумма: <code>{tx['amount']} {tx['currency']}</code>, "
            f"Статус: <code>{tx['status']}</code>\n"
        )

    await message.answer(stats_text, parse_mode="HTML")


# --- ОБРАБОТЧИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # При запуске бота, сохраняем или обновляем информацию о пользователе в БД
    get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )

    welcome_text = (
        f"Привет, {message.from_user.full_name}!\n"
        f"Я бот для пополнения Steam аккаунтов через PlayWallet.\n"
        f"Ваша ссылка для пополнения: https://t.me/your_bot_username?start={message.from_user.id}\n" # Замените your_bot_username
        f"Используйте команды /balance, /deposit, /transactions или /help для начала."
    )
    await message.answer(welcome_text)

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    user = get_user_by_telegram_id(message.from_user.id)
    if user:
        # Показываем баланс из нашей БД
        steam_balance = user['steam_wallet_balance']
        await message.answer(f"Ваш текущий баланс в боте (Steam кошелёк): <code>{steam_balance:.2f} RUB</code>", parse_mode="HTML")
    else:
        await message.answer("Вы не зарегистрированы. Пожалуйста, используйте /start.")

@dp.message(Command("transactions"))
async def cmd_transactions(message: types.Message):
    user = get_user_by_telegram_id(message.from_user.id)
    if user:
        transactions = get_transactions_by_user(message.from_user.id, limit=5)
        if transactions:
            tx_list = "\n".join([
                f"ID: <code>{tx['id']}</code> | {tx['type'].capitalize()}: <code>{tx['amount']} {tx['currency']}</code> | Статус: <code>{tx['status']}</code>"
                for tx in transactions
            ])
            await message.answer(f"<b>Ваши последние транзакции:</b>\n{tx_list}", parse_mode="HTML")
        else:
            await message.answer("У вас пока нет транзакций.")
    else:
        await message.answer("Вы не зарегистрированы. Пожалуйста, используйте /start.")

@dp.message(Command("deposit"))
async def cmd_deposit(message: types.Message):
    # Пример вызова API для инициации депозита
    # Требуется указать сумму, криптовалюту и, возможно, адрес возврата
    # Это упрощённый пример, требует доработки в зависимости от требований API и UX
    example_amount = 10
    example_currency = "TON"
    data_to_send = {
        "amount": example_amount,
        "currency": example_currency,
    }
    api_response = call_playwallet_api("createDeposit", data=data_to_send)
    if api_response:
        # Примерная структура ответа, может отличаться
        deposit_address = api_response.get('address', 'Неизвестен')
        deposit_amount = api_response.get('amount', 'Неизвестна')
        deposit_currency = api_response.get('currency', 'TON')
        instructions = api_response.get('instructions', 'Отправьте криптовалюту на указанный адрес.')

        response_text = (
            f"Для пополнения отправьте <b>{deposit_amount} {deposit_currency}</b> на адрес:\n"
            f"<code>{deposit_address}</code>\n\n"
            f"{instructions}\n\n"
            f"<i>После отправки криптовалюты, пожалуйста, сообщите об этом администратору или используйте команду /check_deposit, если она будет реализована.</i>"
        )
        # Добавляем транзакцию в БД как 'pending'
        add_transaction(
            user_telegram_id=message.from_user.id,
            type='deposit',
            amount=deposit_amount,
            currency=deposit_currency,
            status='pending',
            external_id=api_response.get('externalId'), # Если API возвращает ID транзакции
            description=f"Ожидание пополнения {deposit_amount} {deposit_currency}"
        )
        await message.answer(response_text, parse_mode="HTML")
    else:
        await message.answer("Не удалось создать депозит. Попробуйте позже.")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "/start - Приветственное сообщение\n"
        "/balance - Проверить баланс\n"
        "/deposit - Получить инструкции по пополнению\n"
        "/transactions - Посмотреть последние транзакции\n"
        "/stats - Показать статистику бота (только для администраторов)\n"
        "/help - Показать это сообщение"
    )
    await message.answer(help_text)

# --- Команда для администратора ---
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    await send_admin_stats(message)


# --- ТОЧКА ВХОДА ---

async def main():
    # Инициализируем базу данных при запуске
    init_db()
    logging.info("Бот запускается...")
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())