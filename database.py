import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        username TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id TEXT PRIMARY KEY,
        channel TEXT,
        chat_id TEXT,
        title TEXT,
        type TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        file_id TEXT,
        file_type TEXT,
        name TEXT,
        views INTEGER DEFAULT 0
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS join_requests (
        id TEXT PRIMARY KEY,
        chat_id TEXT,
        user_id TEXT,
        approved INTEGER DEFAULT 0,
        timestamp TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_posts (
        id TEXT PRIMARY KEY,
        channel TEXT,
        file_id TEXT,
        file_type TEXT,
        name TEXT,
        start_time TEXT,
        scheduled_at TEXT,
        processed INTEGER DEFAULT 0,
        created_at TEXT,
        error TEXT
    )
    """)
    
    conn.commit()
    conn.close()

class Database:
    def __init__(self):
        init_db()
        
    def _get_connection(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # Users
    def save_user(self, user_id: str, username: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (user_id, username) 
            VALUES (?, ?) 
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """, (str(user_id), username))
        conn.commit()
        conn.close()

    def get_users(self, limit: int = None) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        if limit:
            cursor.execute("SELECT user_id, username FROM users LIMIT ?", (limit,))
        else:
            cursor.execute("SELECT user_id, username FROM users")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # Channels
    def save_channel(self, id: str, channel: str, chat_id: str, title: str, ch_type: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO channels (id, channel, chat_id, title, type) 
            VALUES (?, ?, ?, ?, ?) 
            ON CONFLICT(id) DO UPDATE SET channel=excluded.channel, chat_id=excluded.chat_id, title=excluded.title, type=excluded.type
        """, (id, channel, chat_id, title, ch_type))
        conn.commit()
        conn.close()

    def delete_channel(self, id: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channels WHERE id = ?", (id,))
        conn.commit()
        conn.close()

    def get_channels(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel, chat_id, title, type FROM channels")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # Movies
    def save_movie(self, code: str, file_id: str, file_type: str, name: str, views: int = 0):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO movies (code, file_id, file_type, name, views) 
            VALUES (?, ?, ?, ?, ?) 
            ON CONFLICT(code) DO UPDATE SET file_id=excluded.file_id, file_type=excluded.file_type, name=excluded.name, views=excluded.views
        """, (code, file_id, file_type, name, views))
        conn.commit()
        conn.close()

    def delete_movie(self, code: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM movies WHERE code = ?", (code,))
        conn.commit()
        conn.close()

    def get_movie(self, code: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT code, file_id, file_type, name, views FROM movies WHERE code = ?", (code,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_movies(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT code, file_id, file_type, name, views FROM movies")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def increment_views(self, code: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE movies SET views = views + 1 WHERE code = ?", (code,))
        conn.commit()
        conn.close()

    # Join requests
    def save_join_request(self, id: str, chat_id: str, user_id: str, approved: bool, timestamp: str):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO join_requests (id, chat_id, user_id, approved, timestamp) 
            VALUES (?, ?, ?, ?, ?) 
            ON CONFLICT(id) DO UPDATE SET chat_id=excluded.chat_id, user_id=excluded.user_id, approved=excluded.approved, timestamp=excluded.timestamp
        """, (id, chat_id, user_id, 1 if approved else 0, timestamp))
        conn.commit()
        conn.close()

    def get_join_request(self, id: str) -> dict:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id, user_id, approved, timestamp FROM join_requests WHERE id = ?", (id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    # Scheduled posts
    def save_scheduled_post(self, id: str, channel: str, file_id: str, file_type: str, name: str, start_time: str, scheduled_at: str, processed: bool, created_at: str, error: str = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scheduled_posts (id, channel, file_id, file_type, name, start_time, scheduled_at, processed, created_at, error) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) 
            ON CONFLICT(id) DO UPDATE SET channel=excluded.channel, file_id=excluded.file_id, file_type=excluded.file_type, name=excluded.name, start_time=excluded.start_time, scheduled_at=excluded.scheduled_at, processed=excluded.processed, created_at=excluded.created_at, error=excluded.error
        """, (id, channel, file_id, file_type, name, start_time, scheduled_at, 1 if processed else 0, created_at, error))
        conn.commit()
        conn.close()

    def update_scheduled_post_processed(self, id: str, processed: bool, error: str = None):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE scheduled_posts SET processed = ?, error = ? WHERE id = ?", (1 if processed else 0, error, id))
        conn.commit()
        conn.close()

    def get_unprocessed_scheduled_posts(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel, file_id, file_type, name, start_time, scheduled_at, processed, created_at, error FROM scheduled_posts WHERE processed = 0")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
