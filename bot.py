"""
SalesTeamBot — Telegram-бот для распределения заказов между исполнителями по тегам.
Полностью рабочий бот с инлайн-кнопками.
"""
import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any, Union
import html

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup
)
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

# ======================================================================
# 1. КОНФИГУРАЦИЯ
# ======================================================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db") 

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан!")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID не задан!")

# ======================================================================
# 2. FSM СОСТОЯНИЯ
# ======================================================================

class AdminStates(StatesGroup):
    waiting_for_tag_name = State()
    waiting_for_service_name = State()
    waiting_for_service_description = State()
    waiting_for_edit_service_name = State()
    waiting_for_edit_service_description = State()
    waiting_for_executor_id = State()
    waiting_for_executor_tags = State()
    waiting_for_service_price = State()
    waiting_for_revise_comment = State()
    waiting_for_executor_name = State()

# ======================================================================
# 3. БАЗА ДАННЫХ
# ======================================================================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                description TEXT,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                UNIQUE(name, tag_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executor_tags (
                executor_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (executor_id, tag_id),
                FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS executor_service_prices (
                executor_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                PRIMARY KEY (executor_id, service_id),
                FOREIGN KEY (executor_id) REFERENCES executors(id) ON DELETE CASCADE,
                FOREIGN KEY (service_id) REFERENCES services(id) ON DELETE CASCADE
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT DEFAULT '',
                description TEXT NOT NULL,
                tag_id INTEGER NOT NULL,
                service_id INTEGER,
                status TEXT DEFAULT 'new',
                executor_id INTEGER,
                revision_comment TEXT,
                price INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (tag_id) REFERENCES tags(id),
                FOREIGN KEY (service_id) REFERENCES services(id),
                FOREIGN KEY (executor_id) REFERENCES executors(id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                telegram_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
        """)
        
        conn.commit()
        conn.close()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # === ТЕГИ ===
    def add_tag(self, name: str) -> bool:
        if not re.match(r'^[A-Za-zА-Яа-яЁё0-9_]{2,32}$', name):
            return False
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO tags (name) VALUES (?)", (name.lower(),))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def delete_tag(self, tag_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    def get_tags(self) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tags ORDER BY name")
        rows = cursor.fetchall()
        conn.close()
        return [{"id": row[0], "name": row[1]} for row in rows]

    def get_tag_by_id(self, tag_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tags WHERE id = ?", (tag_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "name": row[1]}
        return None

    def get_tag_by_name(self, name: str) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tags WHERE lower(name) = lower(?)", (name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "name": row[1]}
        return None

    # === УСЛУГИ ===
    def add_service(self, name: str, tag_id: int, description: str = "") -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO services (name, tag_id, description) VALUES (?, ?, ?)",
                (name, tag_id, description)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def delete_service(self, service_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM services WHERE id = ?", (service_id,))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    def update_service(self, service_id: int, name: str, description: str) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE services SET name = ?, description = ? WHERE id = ?",
                (name, description, service_id)
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    def get_services_by_tag(self, tag_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM services WHERE tag_id = ? ORDER BY name", (tag_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": row[0], "name": row[1], "tag_id": row[2], "description": row[3]} for row in rows]

    def get_service_by_id(self, service_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM services WHERE id = ?", (service_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "name": row[1], "tag_id": row[2], "description": row[3]}
        return None

    # === ИСПОЛНИТЕЛИ ===
    def add_executor(self, telegram_id: int, username: str, full_name: str, tag_ids: List[int]) -> Optional[int]:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO executors (telegram_id, username, full_name, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, username, full_name, datetime.now().isoformat())
            )
            executor_id = cursor.lastrowid
            
            for tag_id in tag_ids:
                cursor.execute(
                    "INSERT INTO executor_tags (executor_id, tag_id) VALUES (?, ?)",
                    (executor_id, tag_id)
                )
            
            conn.commit()
            return executor_id
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    def delete_executor(self, executor_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            # Сначала освобождаем заказы
            cursor.execute(
                "UPDATE orders SET executor_id = NULL, status = 'new' WHERE executor_id = ?",
                (executor_id,)
            )
            # Удаляем исполнителя (остальное удалится каскадно)
            cursor.execute("DELETE FROM executors WHERE id = ?", (executor_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def update_executor_name(self, executor_id: int, full_name: str) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE executors SET full_name = ? WHERE id = ?",
            (full_name, executor_id)
        )
        conn.commit()
        conn.close()

    def get_executor_by_telegram_id(self, telegram_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM executors WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "telegram_id": row[1], "username": row[2], "full_name": row[3], "created_at": row[4]}
        return None

    def get_executor_by_id(self, executor_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM executors WHERE id = ?", (executor_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "telegram_id": row[1], "username": row[2], "full_name": row[3], "created_at": row[4]}
        return None

    def get_all_executors(self) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM executors ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        return [{"id": row[0], "telegram_id": row[1], "username": row[2], "full_name": row[3], "created_at": row[4]} for row in rows]

    def get_executor_tags(self, executor_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.* FROM tags t
            JOIN executor_tags et ON et.tag_id = t.id
            WHERE et.executor_id = ?
            ORDER BY t.name
        """, (executor_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": row[0], "name": row[1]} for row in rows]

    def update_executor_tags(self, executor_id: int, tag_ids: List[int]) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM executor_tags WHERE executor_id = ?", (executor_id,))
        for tag_id in tag_ids:
            cursor.execute(
                "INSERT INTO executor_tags (executor_id, tag_id) VALUES (?, ?)",
                (executor_id, tag_id)
            )
        conn.commit()
        conn.close()

    def get_executors_by_tag(self, tag_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.* FROM executors e
            JOIN executor_tags et ON et.executor_id = e.id
            WHERE et.tag_id = ?
        """, (tag_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": row[0], "telegram_id": row[1], "username": row[2], "full_name": row[3], "created_at": row[4]} for row in rows]

    # === ЦЕНЫ ===
    def set_executor_service_price(self, executor_id: int, service_id: int, price: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO executor_service_prices (executor_id, service_id, price)
            VALUES (?, ?, ?)
            ON CONFLICT(executor_id, service_id) DO UPDATE SET price = ?
        """, (executor_id, service_id, price, price))
        conn.commit()
        conn.close()

    def delete_executor_service_price(self, executor_id: int, service_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM executor_service_prices WHERE executor_id = ? AND service_id = ?",
            (executor_id, service_id)
        )
        conn.commit()
        conn.close()

    def get_executor_service_price(self, executor_id: int, service_id: int) -> Optional[int]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT price FROM executor_service_prices WHERE executor_id = ? AND service_id = ?",
            (executor_id, service_id)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def get_executor_all_prices(self, executor_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                s.id as service_id,
                s.name as service_name,
                t.id as tag_id,
                t.name as tag_name,
                esp.price as executor_price
            FROM services s
            JOIN tags t ON t.id = s.tag_id
            LEFT JOIN executor_service_prices esp ON esp.service_id = s.id AND esp.executor_id = ?
            WHERE s.tag_id IN (SELECT tag_id FROM executor_tags WHERE executor_id = ?)
            ORDER BY t.name, s.name
        """, (executor_id, executor_id))
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "service_id": row[0],
                "service_name": row[1],
                "tag_id": row[2],
                "tag_name": row[3],
                "executor_price": row[4]
            }
            for row in rows
        ]

    # === ЗАКАЗЫ ===
    def create_order(self, title: str, description: str, tag_id: int, service_id: Optional[int] = None) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO orders (title, description, tag_id, service_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'new', ?, ?)",
            (title, description, tag_id, service_id, now, now)
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return order_id

    def get_order(self, order_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "title": row[1], "description": row[2],
                "tag_id": row[3], "service_id": row[4], "status": row[5],
                "executor_id": row[6], "revision_comment": row[7],
                "price": row[8], "created_at": row[9], "updated_at": row[10]
            }
        return None

    def get_orders_by_status(self, status: str) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE status = ? ORDER BY id DESC", (status,))
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": row[0], "title": row[1], "description": row[2],
                "tag_id": row[3], "service_id": row[4], "status": row[5],
                "executor_id": row[6], "revision_comment": row[7],
                "price": row[8], "created_at": row[9], "updated_at": row[10]
            }
            for row in rows
        ]

    def get_available_orders_for_executor(self, executor_id: int) -> List[Dict]:
        tags = self.get_executor_tags(executor_id)
        tag_ids = [t['id'] for t in tags]
        if not tag_ids:
            return []
        
        conn = self._get_conn()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tag_ids))
        cursor.execute(
            f"SELECT * FROM orders WHERE status = 'new' AND tag_id IN ({placeholders}) ORDER BY id DESC",
            tag_ids
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": row[0], "title": row[1], "description": row[2],
                "tag_id": row[3], "service_id": row[4], "status": row[5],
                "executor_id": row[6], "revision_comment": row[7],
                "price": row[8], "created_at": row[9], "updated_at": row[10]
            }
            for row in rows
        ]

    def get_my_orders(self, executor_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM orders WHERE executor_id = ? AND status IN ('in_progress', 'on_review') ORDER BY id DESC",
            (executor_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": row[0], "title": row[1], "description": row[2],
                "tag_id": row[3], "service_id": row[4], "status": row[5],
                "executor_id": row[6], "revision_comment": row[7],
                "price": row[8], "created_at": row[9], "updated_at": row[10]
            }
            for row in rows
        ]

    def get_history_orders(self, executor_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM orders WHERE executor_id = ? AND status = 'completed' ORDER BY id DESC",
            (executor_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": row[0], "title": row[1], "description": row[2],
                "tag_id": row[3], "service_id": row[4], "status": row[5],
                "executor_id": row[6], "revision_comment": row[7],
                "price": row[8], "created_at": row[9], "updated_at": row[10]
            }
            for row in rows
        ]

    def assign_order(self, order_id: int, executor_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        order = self.get_order(order_id)
        price = None
        if order and order["service_id"]:
            price = self.get_executor_service_price(executor_id, order["service_id"])
        
        cursor.execute(
            "UPDATE orders SET status = 'in_progress', executor_id = ?, updated_at = ?, price = ? "
            "WHERE id = ? AND status = 'new'",
            (executor_id, now, price, order_id)
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def submit_order(self, order_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = 'on_review', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), order_id)
        )
        conn.commit()
        conn.close()

    def accept_order(self, order_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = 'completed', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), order_id)
        )
        conn.commit()
        conn.close()

    def revise_order(self, order_id: int, comment: str) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = 'in_progress', revision_comment = ?, updated_at = ? WHERE id = ?",
            (comment, datetime.now().isoformat(), order_id)
        )
        conn.commit()
        conn.close()

    def unassign_order(self, order_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = 'new', executor_id = NULL, updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), order_id)
        )
        conn.commit()
        conn.close()

    def get_order_notifications(self, order_id: int) -> List[Dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM order_notifications WHERE order_id = ?", (order_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"order_id": row[0], "telegram_id": row[1], "message_id": row[2]} for row in rows]

    def clear_order_notifications(self, order_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM order_notifications WHERE order_id = ?", (order_id,))
        conn.commit()
        conn.close()

    def add_order_notification(self, order_id: int, telegram_id: int, message_id: int) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO order_notifications (order_id, telegram_id, message_id) VALUES (?, ?, ?)",
            (order_id, telegram_id, message_id)
        )
        conn.commit()
        conn.close()

db = Database(DB_PATH)

# ======================================================================
# 4. КЛАВИАТУРЫ
# ======================================================================

def e(text: str) -> str:
    """Escapes HTML entities in text"""
    return html.escape(str(text))

def admin_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="🏷 Теги и услуги")
    builder.button(text="👥 Исполнители")
    builder.button(text="📋 Заказы")
    builder.button(text="ℹ️ Помощь")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

def executor_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="📂 Доступные заказы")
    builder.button(text="📌 Мои заказы")
    builder.button(text="📜 История")
    builder.button(text="ℹ️ Помощь")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

def tags_list_kb():
    tags = db.get_tags()
    builder = InlineKeyboardBuilder()
    
    for tag in tags:
        builder.button(
            text=f"📂 {e(tag['name'])}",
            callback_data=f"tag_{tag['id']}"
        )
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="➕ Добавить тег", callback_data="add_tag"))
    return builder.as_markup()

def services_list_kb(tag_id: int):
    services = db.get_services_by_tag(tag_id)
    builder = InlineKeyboardBuilder()
    
    for service in services:
        text = f"📝 {e(service['name'])}"
        if service['description']:
            text += f" - {e(service['description'][:20])}"
        builder.button(
            text=text,
            callback_data=f"service_edit_{service['id']}"
        )
    
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="➕ Добавить услугу", callback_data=f"add_service_{tag_id}"),
        InlineKeyboardButton(text="🗑 Удалить услугу", callback_data=f"service_delete_{tag_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_tags")
    )
    return builder.as_markup()

def executors_list_kb():
    executors = db.get_all_executors()
    builder = InlineKeyboardBuilder()
    
    for ex in executors:
        name = e(ex['full_name'] or f"ID: {ex['telegram_id']}")
        builder.button(
            text=f"👤 {name}",
            callback_data=f"executor_{ex['id']}"
        )
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="➕ Добавить исполнителя", callback_data="add_executor"))
    return builder.as_markup()

def executor_menu_kb(executor_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить имя", callback_data=f"executor_edit_name_{executor_id}")
    builder.button(text="🏷 Изменить теги", callback_data=f"executor_edit_tags_{executor_id}")
    builder.button(text="💰 Цены на услуги", callback_data=f"executor_prices_{executor_id}")
    builder.button(text="🗑 Удалить", callback_data=f"executor_delete_{executor_id}")
    builder.adjust(1)
    return builder.as_markup()

def executor_prices_kb(executor_id: int, prices: List[Dict]):
    builder = InlineKeyboardBuilder()
    
    current_tag = None
    for item in prices:
        if current_tag != item['tag_name']:
            current_tag = item['tag_name']
            builder.button(
                text=f"━━━ {e(current_tag)} ━━━",
                callback_data="separator"
            )
        
        price_text = f"${item['executor_price']}" if item['executor_price'] else "❌"
        builder.button(
            text=f"{e(item['service_name'])} — {price_text}",
            callback_data=f"price_set_{executor_id}_{item['service_id']}"
        )
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_executor_{executor_id}"))
    return builder.as_markup()

def order_take_kb(order_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="🙋 Взять в работу", callback_data=f"take_order_{order_id}")
    return builder.as_markup()

def order_in_progress_kb(order_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Сдать работу", callback_data=f"submit_order_{order_id}")
    builder.button(text="🚫 Отказаться", callback_data=f"decline_order_{order_id}")
    builder.adjust(1)
    return builder.as_markup()

def order_admin_review_kb(order_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"accept_order_{order_id}")
    builder.button(text="✏️ На доработку", callback_data=f"revise_order_{order_id}")
    builder.adjust(1)
    return builder.as_markup()

def orders_status_filter_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🆕 Новые", callback_data="filter_new")
    builder.button(text="🔧 В работе", callback_data="filter_in_progress")
    builder.button(text="🔍 На проверке", callback_data="filter_on_review")
    builder.button(text="✅ Завершенные", callback_data="filter_completed")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="➕ Создать заказ", callback_data="create_order"))
    return builder.as_markup()

# ======================================================================
# 5. ФИЛЬТРЫ
# ======================================================================

class IsAdmin(BaseFilter):
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return event.from_user.id == ADMIN_ID

class IsExecutor(BaseFilter):
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        if event.from_user.id == ADMIN_ID:
            return False
        return db.get_executor_by_telegram_id(event.from_user.id) is not None

# ======================================================================
# 6. ОБЩИЕ ОБРАБОТЧИКИ
# ======================================================================

common_router = Router()

@common_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if user_id == ADMIN_ID:
        await message.answer(
            "👋 Добро пожаловать, администратор!\n\n"
            "Используйте меню для управления:",
            reply_markup=admin_menu()
        )
    else:
        executor = db.get_executor_by_telegram_id(user_id)
        if executor:
            await message.answer(
                f"👋 Добро пожаловать, {e(executor['full_name'])}!\n\n"
                "Выберите раздел в меню:",
                reply_markup=executor_menu()
            )
        else:
            await message.answer(
                "⛔ Доступ запрещен. Обратитесь к администратору."
            )

@common_router.message(F.text == "ℹ️ Помощь")
async def show_help(message: Message):
    user_id = message.from_user.id
    
    if user_id == ADMIN_ID:
        await message.answer(
            "🤖 <b>Помощь для администратора</b>\n\n"
            "🏷 <b>Теги и услуги</b>\n"
            "• Создавайте теги для группировки заказов\n"
            "• Внутри тегов создавайте услуги/товары\n\n"
            "👥 <b>Исполнители</b>\n"
            "• Добавляйте исполнителей по Telegram ID\n"
            "• Назначайте им теги\n"
            "• Устанавливайте цены на каждую услугу\n\n"
            "📋 <b>Заказы</b>\n"
            "• Создавайте заказы через кнопку или по хештегу\n"
            "• Смотрите заказы по статусам\n"
            "• Модерируйте выполненную работу"
        )
    else:
        await message.answer(
            "🤖 <b>Помощь для исполнителя</b>\n\n"
            "📂 <b>Доступные заказы</b>\n"
            "• Заказы по вашим тегам\n"
            "• Нажмите «Взять в работу» чтобы начать\n\n"
            "📌 <b>Мои заказы</b>\n"
            "• Заказы которые вы взяли\n"
            "• Можете сдать работу или отказаться\n\n"
            "📜 <b>История</b>\n"
            "• Ваши завершенные заказы"
        )

# ======================================================================
# 7. АДМИН - ТЕГИ
# ======================================================================

admin_router = Router()
admin_router.message.filter(IsAdmin())

@admin_router.message(F.text == "🏷 Теги и услуги")
async def show_tags(message: Message, state: FSMContext):
    await state.clear()
    tags = db.get_tags()
    
    if not tags:
        await message.answer(
            "Тегов пока нет.\n\nНажмите «➕ Добавить тег», чтобы создать первый.",
            reply_markup=tags_list_kb()
        )
    else:
        text = "📂 <b>Список тегов</b>\n\n"
        for tag in tags:
            services = db.get_services_by_tag(tag['id'])
            count = len(services)
            text += f"• #{e(tag['name'])} ({count} услуг)\n"
        
        await message.answer(text, reply_markup=tags_list_kb())

@admin_router.callback_query(F.data == "add_tag")
async def add_tag_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_tag_name)
    await callback.message.answer(
        "Введите название нового тега (без #), например: дизайн\n\n"
        "Название должно содержать только буквы, цифры или _\n"
        "Или отправьте /cancel для отмены"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_tag_name)
async def add_tag_process(message: Message, state: FSMContext):
    if not message.text or message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Добавление тега отменено.")
        return
    
    name = message.text.strip().lower()
    if db.add_tag(name):
        await message.answer(f"✅ Тег #{e(name)} добавлен!")
        await state.clear()
        await show_tags(message, state)
    else:
        await message.answer(
            f"⚠️ Тег #{e(name)} уже существует или содержит недопустимые символы.\n"
            "Используйте только буквы, цифры или _"
        )

@admin_router.callback_query(F.data.startswith("tag_"))
async def view_tag_services(callback: CallbackQuery):
    tag_id = int(callback.data.split("_")[1])
    tag = db.get_tag_by_id(tag_id)
    
    if not tag:
        await callback.answer("Тег не найден!")
        return
    
    services = db.get_services_by_tag(tag_id)
    
    text = f"📂 <b>Тег #{e(tag['name'])}</b>\n\n"
    if services:
        text += "📋 <b>Услуги:</b>\n"
        for s in services:
            text += f"• {e(s['name'])}"
            if s['description']:
                text += f" — {e(s['description'])}"
            text += "\n"
    else:
        text += "Услуг пока нет.\n\nНажмите «➕ Добавить услугу» чтобы создать."
    
    await callback.message.edit_text(text, reply_markup=services_list_kb(tag_id))
    await callback.answer()

@admin_router.callback_query(F.data.startswith("add_service_"))
async def add_service_start(callback: CallbackQuery, state: FSMContext):
    tag_id = int(callback.data.split("_")[2])
    await state.update_data(tag_id=tag_id)
    await state.set_state(AdminStates.waiting_for_service_name)
    
    await callback.message.answer(
        "Введите название услуги/товара:\n\n"
        "Или отправьте /cancel для отмены"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_service_name)
async def add_service_name(message: Message, state: FSMContext):
    if not message.text or message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Добавление услуги отменено.")
        return
    
    await state.update_data(service_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_service_description)
    await message.answer(
        "Введите описание услуги (или отправьте «-» чтобы пропустить):"
    )

@admin_router.message(AdminStates.waiting_for_service_description)
async def add_service_description(message: Message, state: FSMContext):
    data = await state.get_data()
    description = message.text.strip() if message.text and message.text.strip() != "-" else ""
    
    if db.add_service(data['service_name'], data['tag_id'], description):
        await message.answer(f"✅ Услуга «{e(data['service_name'])}» добавлена!")
    else:
        await message.answer(f"⚠️ Услуга «{e(data['service_name'])}» уже существует.")
    
    await state.clear()
    
    tag = db.get_tag_by_id(data['tag_id'])
    if tag:
        services = db.get_services_by_tag(data['tag_id'])
        text = f"📂 <b>Тег #{e(tag['name'])}</b>\n\n"
        if services:
            text += "📋 <b>Услуги:</b>\n"
            for s in services:
                text += f"• {e(s['name'])}"
                if s['description']:
                    text += f" — {e(s['description'])}"
                text += "\n"
        else:
            text += "Услуг пока нет."
        
        await message.answer(text, reply_markup=services_list_kb(data['tag_id']))

@admin_router.callback_query(F.data.startswith("service_edit_"))
async def edit_service_start(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split("_")[2])
    service = db.get_service_by_id(service_id)
    
    if not service:
        await callback.answer("Услуга не найдена!")
        return
    
    await state.update_data(service_id=service_id, tag_id=service['tag_id'])
    await state.set_state(AdminStates.waiting_for_edit_service_name)
    
    await callback.message.answer(
        f"Редактирование услуги «{e(service['name'])}»\n\n"
        f"Текущее описание: {e(service['description'] or 'нет')}\n\n"
        f"Введите новое название (или отправьте «-» чтобы оставить):"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_edit_service_name)
async def edit_service_name(message: Message, state: FSMContext):
    data = await state.get_data()
    
    if message.text and message.text.strip() != "-":
        await state.update_data(new_name=message.text.strip())
    else:
        service = db.get_service_by_id(data['service_id'])
        await state.update_data(new_name=service['name'] if service else "")
    
    await state.set_state(AdminStates.waiting_for_edit_service_description)
    await message.answer("Введите новое описание (или отправьте «-» чтобы оставить):")

@admin_router.message(AdminStates.waiting_for_edit_service_description)
async def edit_service_description(message: Message, state: FSMContext):
    data = await state.get_data()
    description = message.text.strip() if message.text and message.text.strip() != "-" else ""
    
    if db.update_service(data['service_id'], data['new_name'], description):
        await message.answer("✅ Услуга обновлена!")
    else:
        await message.answer("⚠️ Ошибка при обновлении.")
    
    await state.clear()
    
    tag = db.get_tag_by_id(data['tag_id'])
    if tag:
        services = db.get_services_by_tag(data['tag_id'])
        text = f"📂 <b>Тег #{e(tag['name'])}</b>\n\n"
        if services:
            text += "📋 <b>Услуги:</b>\n"
            for s in services:
                text += f"• {e(s['name'])}"
                if s['description']:
                    text += f" — {e(s['description'])}"
                text += "\n"
        else:
            text += "Услуг пока нет."
        
        await message.answer(text, reply_markup=services_list_kb(data['tag_id']))

@admin_router.callback_query(F.data.startswith("service_delete_"))
async def delete_service(callback: CallbackQuery):
    tag_id = int(callback.data.split("_")[2])
    services = db.get_services_by_tag(tag_id)
    
    if not services:
        await callback.answer("Нет услуг для удаления!")
        return
    
    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(
            text=f"🗑 {e(service['name'])}",
            callback_data=f"service_confirm_delete_{service['id']}"
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"tag_{tag_id}"))
    
    await callback.message.edit_text(
        "Выберите услугу для удаления:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("service_confirm_delete_"))
async def confirm_delete_service(callback: CallbackQuery):
    service_id = int(callback.data.split("_")[3])
    service = db.get_service_by_id(service_id)
    
    if not service:
        await callback.answer("Услуга не найдена!")
        return
    
    if db.delete_service(service_id):
        await callback.answer(f"Услуга «{e(service['name'])}» удалена!")
    else:
        await callback.answer("Не удалось удалить услугу!")
        return
    
    tag = db.get_tag_by_id(service['tag_id'])
    if tag:
        services = db.get_services_by_tag(service['tag_id'])
        text = f"📂 <b>Тег #{e(tag['name'])}</b>\n\n"
        if services:
            text += "📋 <b>Услуги:</b>\n"
            for s in services:
                text += f"• {e(s['name'])}"
                if s['description']:
                    text += f" — {e(s['description'])}"
                text += "\n"
        else:
            text += "Услуг пока нет."
        
        await callback.message.edit_text(text, reply_markup=services_list_kb(service['tag_id']))

@admin_router.callback_query(F.data == "back_to_tags")
async def back_to_tags(callback: CallbackQuery):
    tags = db.get_tags()
    text = "📂 <b>Список тегов</b>\n\n"
    for tag in tags:
        services = db.get_services_by_tag(tag['id'])
        count = len(services)
        text += f"• #{e(tag['name'])} ({count} услуг)\n"
    
    await callback.message.edit_text(text, reply_markup=tags_list_kb())
    await callback.answer()

# ======================================================================
# 8. АДМИН - ИСПОЛНИТЕЛИ
# ======================================================================

@admin_router.message(F.text == "👥 Исполнители")
async def show_executors(message: Message, state: FSMContext):
    await state.clear()
    executors = db.get_all_executors()
    
    if not executors:
        await message.answer(
            "👥 Исполнителей пока нет.\n\nНажмите «➕ Добавить исполнителя», чтобы добавить.",
            reply_markup=executors_list_kb()
        )
    else:
        text = "👥 <b>Список исполнителей</b>\n\n"
        for ex in executors:
            name = e(ex['full_name'] or f"ID: {ex['telegram_id']}")
            tags = db.get_executor_tags(ex['id'])
            tag_names = ", ".join([f"#{e(t['name'])}" for t in tags]) or "нет тегов"
            text += f"• {name}\n  Теги: {tag_names}\n\n"
        
        await message.answer(text, reply_markup=executors_list_kb())

@admin_router.callback_query(F.data == "add_executor")
async def add_executor_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_executor_id)
    await callback.message.answer(
        "Отправьте Telegram ID исполнителя (число):\n\n"
        "Или перешлите сообщение от пользователя.\n"
        "Или отправьте /cancel для отмены"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_executor_id)
async def add_executor_id(message: Message, state: FSMContext):
    if message.text and message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Добавление исполнителя отменено.")
        return
    
    telegram_id = None
    username = None
    full_name = None
    
    if message.forward_from:
        telegram_id = message.forward_from.id
        username = message.forward_from.username
        full_name = message.forward_from.full_name
    elif message.text and message.text.strip().isdigit():
        telegram_id = int(message.text.strip())
    else:
        await message.answer("⚠️ Отправьте числовой Telegram ID или перешлите сообщение.")
        return
    
    existing = db.get_executor_by_telegram_id(telegram_id)
    if existing:
        await message.answer("⚠️ Этот исполнитель уже добавлен.")
        await state.clear()
        return
    
    await state.update_data(
        telegram_id=telegram_id,
        username=username,
        full_name=full_name or f"ID: {telegram_id}"
    )
    
    tags = db.get_tags()
    if not tags:
        await message.answer("⚠️ Сначала создайте теги в разделе «🏷 Теги и услуги».")
        await state.clear()
        return
    
    builder = InlineKeyboardBuilder()
    for tag in tags:
        builder.button(text=f"▫️ {e(tag['name'])}", callback_data=f"exec_tag_toggle_{tag['id']}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="exec_tags_done"))
    
    await state.set_state(AdminStates.waiting_for_executor_tags)
    await state.update_data(selected_tags=set(), is_edit=False)
    
    await message.answer(
        "Выберите теги для исполнителя, затем нажмите «✅ Готово»:",
        reply_markup=builder.as_markup()
    )

@admin_router.callback_query(AdminStates.waiting_for_executor_tags, F.data.startswith("exec_tag_toggle_"))
async def toggle_executor_tag(callback: CallbackQuery, state: FSMContext):
    tag_id = int(callback.data.split("_")[3])
    data = await state.get_data()
    selected = data.get('selected_tags', set())
    
    if tag_id in selected:
        selected.remove(tag_id)
    else:
        selected.add(tag_id)
    
    await state.update_data(selected_tags=selected)
    
    tags = db.get_tags()
    builder = InlineKeyboardBuilder()
    for tag in tags:
        mark = "✅" if tag['id'] in selected else "▫️"
        builder.button(text=f"{mark} {e(tag['name'])}", callback_data=f"exec_tag_toggle_{tag['id']}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="exec_tags_done"))
    
    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
    await callback.answer()

@admin_router.callback_query(AdminStates.waiting_for_executor_tags, F.data == "exec_tags_done")
async def finish_executor_tags(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_tags', set())
    is_edit = data.get('is_edit', False)
    
    if not selected:
        await callback.answer("Выберите хотя бы один тег!", show_alert=True)
        return
    
    if is_edit:
        executor_id = data.get('executor_id')
        db.update_executor_tags(executor_id, list(selected))
        await state.clear()
        await callback.message.edit_text("✅ Теги исполнителя обновлены!")
        await show_executors(callback.message, state)
        await callback.answer()
    else:
        executor_id = db.add_executor(
            data['telegram_id'],
            data.get('username'),
            data.get('full_name'),
            list(selected)
        )
        await state.clear()
        
        if executor_id:
            await callback.message.edit_text("✅ Исполнитель добавлен!")
            await show_executors(callback.message, state)
        else:
            await callback.message.answer("⚠️ Не удалось добавить исполнителя.")
    
    await callback.answer()

# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ИСПОЛНИТЕЛЕЙ ---

@admin_router.callback_query(F.data.startswith("executor_") & 
                             ~F.data.startswith("executor_edit_name_") & 
                             ~F.data.startswith("executor_edit_tags_") & 
                             ~F.data.startswith("executor_prices_") & 
                             ~F.data.startswith("executor_delete_") & 
                             ~F.data.startswith("executor_confirm_delete_") & 
                             ~F.data.startswith("back_to_executor_"))
async def view_executor(callback: CallbackQuery):
    executor_id = int(callback.data.split("_")[1])
    executor = db.get_executor_by_id(executor_id)
    
    if not executor:
        await callback.answer("Исполнитель не найден!")
        return
    
    tags = db.get_executor_tags(executor_id)
    prices = db.get_executor_all_prices(executor_id)
    
    name = e(executor['full_name'] or f"ID: {executor['telegram_id']}")
    tag_names = ", ".join([f"#{e(t['name'])}" for t in tags]) or "нет тегов"
    
    text = f"👤 <b>{name}</b>\n"
    text += f"🆔 {executor['telegram_id']}\n"
    text += f"🏷 Теги: {tag_names}\n\n"
    
    if prices:
        text += "💰 <b>Цены на услуги:</b>\n"
        current_tag = None
        for item in prices:
            if current_tag != item['tag_name']:
                current_tag = item['tag_name']
                text += f"\n#{e(current_tag)}:\n"
            price_text = f"${item['executor_price']}" if item['executor_price'] else "❌"
            text += f"  • {e(item['service_name'])} — {price_text}\n"
    
    await callback.message.edit_text(text, reply_markup=executor_menu_kb(executor_id))
    await callback.answer()

# ---------- ИЗМЕНЕНИЕ ИМЕНИ ----------

@admin_router.callback_query(F.data.startswith("executor_edit_name_"))
async def edit_executor_name_start(callback: CallbackQuery, state: FSMContext):
    executor_id = int(callback.data.split("_")[3])
    await state.update_data(executor_id=executor_id)
    await state.set_state(AdminStates.waiting_for_executor_name)
    await callback.message.answer(
        "Введите новое имя для исполнителя:\n\n"
        "Или отправьте /cancel для отмены"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_executor_name)
async def edit_executor_name_process(message: Message, state: FSMContext):
    if not message.text or message.text.startswith('/'):
        await state.clear()
        await message.answer("❌ Изменение имени отменено.")
        return
    
    data = await state.get_data()
    db.update_executor_name(data['executor_id'], message.text.strip())
    await state.clear()
    
    await message.answer("✅ Имя исполнителя обновлено!")
    await show_executors(message, state)

# ---------- ИЗМЕНЕНИЕ ТЕГОВ ----------

@admin_router.callback_query(F.data.startswith("executor_edit_tags_"))
async def edit_executor_tags_start(callback: CallbackQuery, state: FSMContext):
    executor_id = int(callback.data.split("_")[3])
    executor = db.get_executor_by_id(executor_id)
    
    if not executor:
        await callback.answer("Исполнитель не найден!")
        return
    
    current_tags = db.get_executor_tags(executor_id)
    current_ids = {t['id'] for t in current_tags}
    
    await state.update_data(
        executor_id=executor_id,
        selected_tags=current_ids,
        telegram_id=executor['telegram_id'],
        username=executor['username'],
        full_name=executor['full_name'],
        is_edit=True
    )
    await state.set_state(AdminStates.waiting_for_executor_tags)
    
    tags = db.get_tags()
    if not tags:
        await callback.message.answer("⚠️ Нет доступных тегов.")
        await state.clear()
        return
    
    builder = InlineKeyboardBuilder()
    for tag in tags:
        mark = "✅" if tag['id'] in current_ids else "▫️"
        builder.button(text=f"{mark} {e(tag['name'])}", callback_data=f"exec_tag_toggle_{tag['id']}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✅ Готово", callback_data="exec_tags_done"))
    
    await callback.message.edit_text(
        "Выберите теги для исполнителя:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# ---------- УПРАВЛЕНИЕ ЦЕНАМИ ----------

@admin_router.callback_query(F.data.startswith("executor_prices_"))
async def manage_executor_prices(callback: CallbackQuery):
    executor_id = int(callback.data.split("_")[2])
    executor = db.get_executor_by_id(executor_id)
    
    if not executor:
        await callback.answer("Исполнитель не найден!")
        return
    
    prices = db.get_executor_all_prices(executor_id)
    
    if not prices:
        await callback.message.answer(
            "⚠️ У исполнителя нет доступных услуг.\n"
            "Сначала добавьте услуги в теги, которые назначены исполнителю."
        )
        await callback.answer()
        return
    
    text = f"💰 <b>Цены исполнителя {e(executor['full_name'] or '')}</b>\n\n"
    text += "Нажмите на услугу чтобы установить/изменить цену:\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=executor_prices_kb(executor_id, prices)
    )
    await callback.answer()

# ---------- УСТАНОВКА ЦЕНЫ ----------

@admin_router.callback_query(F.data.startswith("price_set_"))
async def set_price_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    executor_id = int(parts[2])
    service_id = int(parts[3])
    
    service = db.get_service_by_id(service_id)
    current_price = db.get_executor_service_price(executor_id, service_id)
    
    await state.update_data(executor_id=executor_id, service_id=service_id)
    await state.set_state(AdminStates.waiting_for_service_price)
    
    price_text = f" (текущая: ${current_price})" if current_price else ""
    
    await callback.message.answer(
        f"Введите цену в $ для услуги «{e(service['name'])}»{price_text}:\n"
        f"(или отправьте «0» чтобы удалить цену)"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_service_price)
async def set_price_process(message: Message, state: FSMContext):
    if not message.text:
        return
    
    data = await state.get_data()
    price_text = message.text.strip()
    
    try:
        price = float(price_text)
        if price_text == "0":
            db.delete_executor_service_price(data['executor_id'], data['service_id'])
            await message.answer("✅ Цена удалена.")
        elif price > 0:
            db.set_executor_service_price(data['executor_id'], data['service_id'], int(price))
            await message.answer(f"✅ Цена установлена: ${int(price)}")
        else:
            await message.answer("⚠️ Цена должна быть положительной.")
            return
    except ValueError:
        await message.answer("⚠️ Введите число или 0 для удаления.")
        return
    
    await state.clear()
    
    executor = db.get_executor_by_id(data['executor_id'])
    if executor:
        prices = db.get_executor_all_prices(data['executor_id'])
        if prices:
            text = f"💰 <b>Обновленный прайс {e(executor['full_name'] or '')}</b>\n\n"
            await message.answer(
                text,
                reply_markup=executor_prices_kb(data['executor_id'], prices)
            )

# ---------- УДАЛЕНИЕ ИСПОЛНИТЕЛЯ ----------

@admin_router.callback_query(F.data.startswith("executor_delete_"))
async def delete_executor_final(callback: CallbackQuery):
    executor_id = int(callback.data.split("_")[2])
    
    # Получаем исполнителя
    executor = db.get_executor_by_id(executor_id)
    if not executor:
        await callback.answer("Исполнитель не найден!", show_alert=True)
        return
    
    name = executor['full_name'] or f"ID: {executor['telegram_id']}"
    
    try:
        # ПРЯМОЕ УДАЛЕНИЕ ЧЕРЕЗ SQL
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Освобождаем заказы
        cursor.execute("UPDATE orders SET executor_id = NULL, status = 'new' WHERE executor_id = ?", (executor_id,))
        # Удаляем исполнителя
        cursor.execute("DELETE FROM executors WHERE id = ?", (executor_id,))
        
        conn.commit()
        conn.close()
        
        await callback.message.edit_text(f"✅ Исполнитель <b>{name}</b> удален!")
        await callback.answer("✅ Удалено!")
        
        # Обновляем список
        executors = db.get_all_executors()
        if executors:
            text = "👥 <b>Список исполнителей</b>\n\n"
            for ex in executors:
                name2 = ex['full_name'] or f"ID: {ex['telegram_id']}"
                tags = db.get_executor_tags(ex['id'])
                tag_names = ", ".join([f"#{t['name']}" for t in tags]) or "нет тегов"
                text += f"• {name2}\n  Теги: {tag_names}\n\n"
            await callback.message.answer(text, reply_markup=executors_list_kb())
        else:
            await callback.message.answer(
                "👥 Исполнителей пока нет.\n\nНажмите «➕ Добавить исполнителя», чтобы добавить.",
                reply_markup=executors_list_kb()
            )
            
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        await callback.answer("❌ Ошибка!", show_alert=True)
        

# ======================================================================
# 9. АДМИН - ЗАКАЗЫ
# ======================================================================

@admin_router.message(F.text == "📋 Заказы")
async def show_orders(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📋 <b>Управление заказами</b>\n\n"
        "Выберите статус для просмотра:",
        reply_markup=orders_status_filter_kb()
    )

@admin_router.callback_query(F.data.startswith("filter_"))
async def filter_orders(callback: CallbackQuery):
    status_map = {
        "new": "🆕 Новые",
        "in_progress": "🔧 В работе",
        "on_review": "🔍 На проверке",
        "completed": "✅ Завершенные"
    }
    
    status = callback.data.split("_")[1]
    orders = db.get_orders_by_status(status)
    
    if not orders:
        await callback.message.answer(f"Заказов со статусом «{status_map.get(status, status)}» нет.")
        await callback.answer()
        return
    
    for order in orders:
        tag = db.get_tag_by_id(order['tag_id'])
        service = db.get_service_by_id(order['service_id']) if order['service_id'] else None
        executor = db.get_executor_by_id(order['executor_id']) if order['executor_id'] else None
        
        text = f"📦 <b>Заказ #{order['id']}</b>\n"
        text += f"Статус: {status_map.get(status, status)}\n"
        if order['title']:
            text += f"Название: {e(order['title'])}\n"
        text += f"Тег: #{e(tag['name']) if tag else '?'}\n"
        if service:
            text += f"Услуга: {e(service['name'])}\n"
        if order['price']:
            text += f"💰 Цена: ${order['price']}\n"
        if executor:
            text += f"Исполнитель: {e(executor['full_name'])}\n"
        text += f"\n{e(order['description'][:200])}"
        
        if status == "on_review":
            kb = order_admin_review_kb(order['id'])
        else:
            kb = None
        
        await callback.message.answer(text, reply_markup=kb)
    
    await callback.answer()

@admin_router.callback_query(F.data == "create_order")
async def create_order_start(callback: CallbackQuery):
    await callback.message.answer(
        "📝 <b>Создание заказа</b>\n\n"
        "Просто отправьте сообщение с хештегом тега.\n"
        "Например: «Нужен баннер #дизайн»"
    )
    await callback.answer()

@admin_router.message(F.text & ~F.text.startswith("/"))
async def create_order_by_hashtag(message: Message):
    text = message.text.strip()
    
    if text in ["🏷 Теги и услуги", "👥 Исполнители", "📋 Заказы", "ℹ️ Помощь"]:
        return
    
    hashtags = re.findall(r"#(\w+)", text)
    if not hashtags:
        await message.answer(
            "❌ Не найден хештег.\n\n"
            "Чтобы создать заказ, добавьте хештег, например:\n"
            "«Нужен баннер #дизайн»"
        )
        return
    
    tag = None
    for h in hashtags:
        tag = db.get_tag_by_name(h)
        if tag:
            break
    
    if not tag:
        await message.answer(
            f"⚠️ Тег не найден среди: {', '.join(['#'+h for h in hashtags])}\n"
            "Создайте тег в разделе «🏷 Теги и услуги»."
        )
        return
    
    order_id = db.create_order("", text, tag['id'])
    executors = db.get_executors_by_tag(tag['id'])
    
    for ex in executors:
        try:
            await message.bot.send_message(
                ex['telegram_id'],
                f"🆕 Новый заказ #{order_id} по тегу #{e(tag['name'])}\n\n"
                f"{e(text[:200])}\n\n"
                f"Нажмите кнопку чтобы взять в работу:",
                reply_markup=order_take_kb(order_id)
            )
        except:
            pass
    
    await message.answer(
        f"✅ Заказ #{order_id} создан по тегу #{e(tag['name'])}!\n"
        f"Уведомлено {len(executors)} исполнителей."
    )

@admin_router.callback_query(F.data.startswith("accept_order_"))
async def accept_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    order = db.get_order(order_id)
    
    if not order or order['status'] != 'on_review':
        await callback.answer("Заказ уже обработан!", show_alert=True)
        return
    
    db.accept_order(order_id)
    
    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Работа принята! Заказ завершен."
    )
    await callback.answer("✅ Заказ принят!")
    
    if order['executor_id']:
        executor = db.get_executor_by_id(order['executor_id'])
        if executor:
            try:
                await callback.bot.send_message(
                    executor['telegram_id'],
                    f"✅ Ваша работа по заказу #{order_id} принята администратором!"
                )
            except:
                pass

@admin_router.callback_query(F.data.startswith("revise_order_"))
async def revise_order_start(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split("_")[2])
    order = db.get_order(order_id)
    
    if not order or order['status'] != 'on_review':
        await callback.answer("Заказ уже обработан!", show_alert=True)
        return
    
    await state.update_data(order_id=order_id)
    await state.set_state(AdminStates.waiting_for_revise_comment)
    await callback.message.answer(
        f"✏️ <b>Доработка заказа #{order_id}</b>\n\n"
        "Напишите комментарий с правками для исполнителя:"
    )
    await callback.answer()

@admin_router.message(AdminStates.waiting_for_revise_comment)
async def revise_order_process(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Напишите комментарий.")
        return
    
    data = await state.get_data()
    order_id = data['order_id']
    comment = message.text
    
    db.revise_order(order_id, comment)
    await state.clear()
    
    await message.answer(f"✅ Заказ #{order_id} отправлен на доработку.")
    
    order = db.get_order(order_id)
    if order and order['executor_id']:
        executor = db.get_executor_by_id(order['executor_id'])
        if executor:
            try:
                await message.bot.send_message(
                    executor['telegram_id'],
                    f"✏️ Заказ #{order_id} отправлен на доработку.\n\n"
                    f"Комментарий: {e(comment)}"
                )
            except:
                pass

# ======================================================================
# 10. ИСПОЛНИТЕЛЬ
# ======================================================================

executor_router = Router()
executor_router.message.filter(IsExecutor())

@executor_router.message(F.text == "📂 Доступные заказы")
async def available_orders(message: Message, state: FSMContext):
    await state.clear()
    
    executor = db.get_executor_by_telegram_id(message.from_user.id)
    if not executor:
        await message.answer("⛔ Вы не зарегистрированы как исполнитель.")
        return
    
    orders = db.get_available_orders_for_executor(executor['id'])
    
    if not orders:
        await message.answer("📂 Нет доступных заказов по вашим тегам.")
        return
    
    for order in orders:
        tag = db.get_tag_by_id(order['tag_id'])
        service = db.get_service_by_id(order['service_id']) if order['service_id'] else None
        
        text = f"📦 <b>Заказ #{order['id']}</b>\n"
        if order['title']:
            text += f"Название: {e(order['title'])}\n"
        text += f"Тег: #{e(tag['name']) if tag else '?'}\n"
        if service:
            text += f"Услуга: {e(service['name'])}\n"
        text += f"\n{e(order['description'][:200])}"
        
        await message.answer(text, reply_markup=order_take_kb(order['id']))

@executor_router.message(F.text == "📌 Мои заказы")
async def my_orders(message: Message, state: FSMContext):
    await state.clear()
    
    executor = db.get_executor_by_telegram_id(message.from_user.id)
    if not executor:
        await message.answer("⛔ Вы не зарегистрированы как исполнитель.")
        return
    
    orders = db.get_my_orders(executor['id'])
    
    if not orders:
        await message.answer("📌 У вас нет заказов в работе.")
        return
    
    for order in orders:
        tag = db.get_tag_by_id(order['tag_id'])
        service = db.get_service_by_id(order['service_id']) if order['service_id'] else None
        
        status_label = "🔧 В работе" if order['status'] == 'in_progress' else "🔍 На проверке"
        
        text = f"📦 <b>Заказ #{order['id']}</b> ({status_label})\n"
        if order['title']:
            text += f"Название: {e(order['title'])}\n"
        text += f"Тег: #{e(tag['name']) if tag else '?'}\n"
        if service:
            text += f"Услуга: {e(service['name'])}\n"
        if order['price']:
            text += f"💰 Цена: ${order['price']}\n"
        if order['revision_comment']:
            text += f"\n⚠️ Комментарий по доработке:\n{e(order['revision_comment'])}\n"
        text += f"\n{e(order['description'][:200])}"
        
        if order['status'] == 'in_progress':
            kb = order_in_progress_kb(order['id'])
        else:
            kb = None
        
        await message.answer(text, reply_markup=kb)

@executor_router.message(F.text == "📜 История")
async def history_orders(message: Message, state: FSMContext):
    await state.clear()
    
    executor = db.get_executor_by_telegram_id(message.from_user.id)
    if not executor:
        await message.answer("⛔ Вы не зарегистрированы как исполнитель.")
        return
    
    orders = db.get_history_orders(executor['id'])
    
    if not orders:
        await message.answer("📜 У вас нет завершенных заказов.")
        return
    
    for order in orders:
        tag = db.get_tag_by_id(order['tag_id'])
        service = db.get_service_by_id(order['service_id']) if order['service_id'] else None
        
        text = f"📦 <b>Заказ #{order['id']}</b> ✅ Завершен\n"
        if order['title']:
            text += f"Название: {e(order['title'])}\n"
        text += f"Тег: #{e(tag['name']) if tag else '?'}\n"
        if service:
            text += f"Услуга: {e(service['name'])}\n"
        if order['price']:
            text += f"💰 Цена: ${order['price']}\n"
        text += f"\n{e(order['description'][:200])}"
        
        await message.answer(text)

# ---------- ДЕЙСТВИЯ ИСПОЛНИТЕЛЯ ----------

@executor_router.callback_query(F.data.startswith("take_order_"))
async def take_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    executor = db.get_executor_by_telegram_id(callback.from_user.id)
    
    if not executor:
        await callback.answer("Вы не зарегистрированы как исполнитель!", show_alert=True)
        return
    
    order = db.get_order(order_id)
    if not order or order['status'] != 'new':
        await callback.answer("Этот заказ уже занят!", show_alert=True)
        return
    
    if db.assign_order(order_id, executor['id']):
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ Вы взяли заказ в работу!"
        )
        await callback.answer("✅ Заказ взят!")
        
        name = executor['full_name'] or f"ID: {executor['telegram_id']}"
        await callback.bot.send_message(
            ADMIN_ID,
            f"🔧 Исполнитель {e(name)} взял заказ #{order_id}"
        )
    else:
        await callback.answer("❌ Не удалось взять заказ!", show_alert=True)

@executor_router.callback_query(F.data.startswith("submit_order_"))
async def submit_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    executor = db.get_executor_by_telegram_id(callback.from_user.id)
    order = db.get_order(order_id)
    
    if not executor or not order or order['executor_id'] != executor['id']:
        await callback.answer("Это не ваш заказ!", show_alert=True)
        return
    
    if order['status'] != 'in_progress':
        await callback.answer("Заказ не в работе!", show_alert=True)
        return
    
    db.submit_order(order_id)
    
    await callback.message.edit_text(
        callback.message.text + "\n\n📤 Работа отправлена на проверку!"
    )
    await callback.answer("✅ Работа отправлена!")
    
    await callback.bot.send_message(
        ADMIN_ID,
        f"📥 Исполнитель {e(executor['full_name'])} сдал работу по заказу #{order_id}"
    )

@executor_router.callback_query(F.data.startswith("decline_order_"))
async def decline_order(callback: CallbackQuery):
    order_id = int(callback.data.split("_")[2])
    executor = db.get_executor_by_telegram_id(callback.from_user.id)
    order = db.get_order(order_id)
    
    if not executor or not order or order['executor_id'] != executor['id']:
        await callback.answer("Это не ваш заказ!", show_alert=True)
        return
    
    db.unassign_order(order_id)
    
    await callback.message.edit_text(
        callback.message.text + "\n\n🚫 Вы отказались от заказа."
    )
    await callback.answer("❌ Отказ принят!")
    
    await callback.bot.send_message(
        ADMIN_ID,
        f"🚫 Исполнитель {e(executor['full_name'])} отказался от заказа #{order_id}"
    )

# ======================================================================
# 11. ОБРАБОТКА SEPARATOR
# ======================================================================

@admin_router.callback_query(F.data == "separator")
async def handle_separator(callback: CallbackQuery):
    await callback.answer("Это заголовок раздела")

@executor_router.callback_query(F.data == "separator")
async def handle_separator_executor(callback: CallbackQuery):
    await callback.answer("Это заголовок раздела")

# ======================================================================
# 12. HTTP-СЕРВЕР ДЛЯ RENDER
# ======================================================================

import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_http_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"✅ HTTP сервер запущен на порту {port}")
    server.serve_forever()

# ======================================================================
# 13. ЗАПУСК
# ======================================================================

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Запускаем HTTP-сервер в фоновом потоке
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(common_router)
    dp.include_router(admin_router)
    dp.include_router(executor_router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
