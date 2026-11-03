"""
SoundSet — SoundPad personalizable hecho con Streamlit.

Ejecutar:
    pip install -r requirements.txt
    streamlit run app.py

Estructura:
    app.py
    requirements.txt
    sounds/
        default/     -> 10 sonidos de ejemplo (se generan solos si no existen)
        users/       -> sonidos que sube cada usuario (una carpeta por usuario)
        community/   -> copias de los botones publicados en la comunidad
    soundset.db      -> base de datos SQLite (se crea sola)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import math
import re
import secrets
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import streamlit as st
import streamlit.components.v1 as components

try:
    from mutagen import File as MutagenFile
except Exception:  # mutagen es opcional (solo sirve para leer la duración de los MP3)
    MutagenFile = None

try:
    from PIL import Image, ImageOps
except Exception:  # Pillow viene con Streamlit, pero por si acaso
    Image = ImageOps = None

# ------------------------------------------------------------------ rutas y constantes
BASE_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = BASE_DIR / "sounds"
DEFAULT_DIR = SOUNDS_DIR / "default"
USER_SOUNDS_DIR = SOUNDS_DIR / "users"
COMMUNITY_DIR = SOUNDS_DIR / "community"
DB_PATH = BASE_DIR / "soundset.db"
SR = 22050  # frecuencia de muestreo de los sonidos de ejemplo
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PER_PAGE = 12

COLORS = {
    "Rojo": "#e53935",
    "Naranja": "#ff6d00",
    "Amarillo": "#f9b800",
    "Verde": "#2e9e4f",
    "Cian": "#00a5bd",
    "Azul": "#1e6fe0",
    "Índigo": "#3f51b5",
    "Morado": "#8e24aa",
    "Rosa": "#e91e7a",
    "Café": "#7a4a2e",
    "Gris": "#5f6b75",
    "Negro": "#1c1a19",
}

PAGE_PAD = ":material/grid_view: Mi SoundPad"
PAGE_COMMUNITY = ":material/groups: Comunidad"
PAGE_ADD = ":material/add_circle: Agregar botón"
PAGE_EDIT = ":material/tune: Editar botones"
PAGE_PROFILE = ":material/person: Mi perfil"
# Comunidad va justo debajo de Mi SoundPad para llegar más rápido
PAGES = [PAGE_PAD, PAGE_COMMUNITY, PAGE_ADD, PAGE_EDIT, PAGE_PROFILE]

st.set_page_config(
    page_title="SoundSet",
    page_icon=":material/graphic_eq:",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ================================================================== ÍCONOS (dibujos SVG)
def _star(points: int, r_out: float, r_in: float) -> str:
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(-90 + i * 180 / points)
        pts.append(f"{12 + r * math.cos(a):.2f} {12 + r * math.sin(a):.2f}")
    return f'<path d="M{" L".join(pts)} Z"/>'


# clave -> (nombre visible, trazos SVG en una grilla de 24x24)
ICONS: dict[str, tuple[str, str]] = {
    "alarm": ("Despertador", '<circle cx="12" cy="13" r="7"/><path d="M12 9.5V13l2.5 2"/>'
                             '<path d="M5 4 2.5 6.5M19 4l2.5 2.5"/>'),
    "horn": ("Megáfono", '<path d="M3 10v4h3l9 5V5l-9 5H3z"/><path d="M18 9a4 4 0 0 1 0 6"/>'
                         '<path d="M6 14l1 5h3l-1-4"/>'),
    "bolt": ("Rayo", '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>'),
    "coin": ("Moneda", '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5"/><path d="M12 9.5v5"/>'),
    "burst": ("Explosión", _star(8, 10, 4.6)),
    "clap": ("Aplauso", '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12"/><path d="M11 11V4a1.5 1.5 0 0 1 3 0v7"/>'
                        '<path d="M14 11V5.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-.5a6 6 0 0 1-4.6-2.2'
                        'L3.5 16a1.5 1.5 0 0 1 2.3-1.9L8 16"/><path d="M19.5 3.5 21 2M20 7h2"/>'),
    "bell": ("Campana", '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4l2-2z"/><path d="M10 21a2 2 0 0 0 4 0"/>'),
    "spring": ("Rebote", '<circle cx="17" cy="6.5" r="3"/><path d="M3 19c1.5-6 5-10.5 10-11"/>'
                         '<path d="M3 21h18"/>'),
    "drum": ("Tambor", '<ellipse cx="12" cy="10" rx="8" ry="3"/>'
                       '<path d="M4 10v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/><path d="M8 12.6V19.6M16 12.6V19.6"/>'
                       '<path d="M15 3l4 4.5M9 3 5 7.5"/>'),
    "siren": ("Sirena", '<path d="M7 18v-5a5 5 0 0 1 10 0v5"/><rect x="4" y="18" width="16" height="3" rx="1"/>'
                        '<path d="M12 11v3"/><path d="M2.5 9 4.5 10M21.5 9l-2 1M5 4l1.5 1.5M19 4l-1.5 1.5"/>'),
    "speaker": ("Parlante", '<path d="M4 9.5h3.5L12 5v14l-4.5-4.5H4z"/>'
                            '<path d="M15.5 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11"/>'),
    "wave": ("Onda", '<path d="M3 12h1M7 8v8M11 4v16M15 8v8M19 10.5v3M21 12h0"/>'),
    "music": ("Nota", '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
    "mic": ("Micrófono", '<rect x="9" y="3" width="6" height="11" rx="3"/>'
                         '<path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>'),
    "star": ("Estrella", _star(5, 9.5, 4.2)),
    "heart": ("Corazón", '<path d="M12 20s-7.5-4.6-9-9.3C2 7.4 4.2 4.5 7.3 4.5c2 0 3.6 1.1 4.7 2.8'
                         ' 1.1-1.7 2.7-2.8 4.7-2.8 3.1 0 5.3 2.9 4.3 6.2C19.5 15.4 12 20 12 20z"/>'),
    "smile": ("Sonrisa", '<circle cx="12" cy="12" r="9"/><path d="M8 14.5a5 5 0 0 0 8 0"/>'
                         '<path d="M9 9.5h.01M15 9.5h.01" stroke-width="2.6"/>'),
    "laugh": ("Risa", '<circle cx="12" cy="12" r="9"/><path d="M7.5 13h9a4.5 4.5 0 0 1-9 0z"/>'
                      '<path d="M7.5 10 9 8.5l1.5 1.5M13.5 10 15 8.5l1.5 1.5"/>'),
    "sad": ("Triste", '<circle cx="12" cy="12" r="9"/><path d="M8 16.5a5 5 0 0 1 8 0"/>'
                      '<path d="M9 10h.01M15 10h.01" stroke-width="2.6"/>'),
    "skull": ("Calavera", '<path d="M5 15.5V11a7 7 0 0 1 14 0v4.5a1.5 1.5 0 0 1-1.5 1.5H16v3H8v-3H6.5'
                          'A1.5 1.5 0 0 1 5 15.5z"/><circle cx="9" cy="11.5" r="1.6"/>'
                          '<circle cx="15" cy="11.5" r="1.6"/><path d="M11 20v-2M13 20v-2"/>'),
    "ghost": ("Fantasma", '<path d="M5 21V11a7 7 0 0 1 14 0v10l-2.3-1.8L14.3 21 12 19.2 9.7 21l-2.4-1.8L5 21z"/>'
                          '<path d="M9.5 11h.01M14.5 11h.01" stroke-width="2.6"/>'),
    "fire": ("Fuego", '<path d="M12 21c-3.9 0-7-2.8-7-6.5 0-3 2-5 3.5-6.5.3 1.6 1.2 2.6 2.2 3C10 8 11 5 14 3'
                      'c0 3 5 5.5 5 11.5C19 18.2 15.9 21 12 21z"/>'
                      '<path d="M12 21c-1.7 0-3-1.2-3-2.8 0-1.6 1.5-2.6 3-4.2 1.5 1.6 3 2.6 3 4.2 0 1.6-1.3 2.8-3 2.8z"/>'),
    "bomb": ("Bomba", '<circle cx="10.5" cy="14" r="6.5"/><path d="M15.2 9.3 17.5 7"/><path d="M16.3 5.8l1.9 1.9"/>'
                      '<path d="M19.5 4.5 21 3M20.2 7.3h1.8M17.2 3.3V1.5"/>'),
    "rocket": ("Cohete", '<path d="M12 2.5c3 2 4.5 5.5 4.5 9.5l-2 4h-5l-2-4c0-4 1.5-7.5 4.5-9.5z"/>'
                         '<circle cx="12" cy="9.5" r="1.8"/>'
                         '<path d="M7.5 12 5 15v3l3.5-1.5M16.5 12 19 15v3l-3.5-1.5M10.5 19l1.5 2.5 1.5-2.5"/>'),
    "gamepad": ("Control", '<path d="M7 7h10a5 5 0 0 1 4.9 6l-.6 3a2.5 2.5 0 0 1-4.3 1.2L15 15H9l-2 2.2'
                           'A2.5 2.5 0 0 1 2.7 16l-.6-3A5 5 0 0 1 7 7z"/>'
                           '<path d="M7 10v4M5 12h4"/><path d="M15.5 11h.01M17.5 13h.01" stroke-width="2.6"/>'),
    "trophy": ("Trofeo", '<path d="M8 4h8v5a4 4 0 0 1-8 0V4z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4"/>'
                         '<path d="M12 13v4M9 21h6M10 17h4v4h-4z"/>'),
    "thumb": ("Like", '<path d="M7 10v11H4V10h3z"/><path d="M7 10l4-7a2 2 0 0 1 2.5 2.4L12.5 9H19a2 2 0 0 1 2 2.3'
                      'l-1.2 7.5A2.5 2.5 0 0 1 17.3 21H7"/>'),
    "question": ("Pregunta", '<circle cx="12" cy="12" r="9"/>'
                             '<path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6"/>'
                             '<path d="M12 17h.01" stroke-width="2.6"/>'),
    "cat": ("Gato", '<path d="M5 20v-8.5L4 4l4.5 3.5h7L20 4l-1 7.5V20z"/>'
                    '<path d="M9.5 13h.01M14.5 13h.01" stroke-width="2.6"/><path d="M11 16.5h2"/>'),
    "car": ("Auto", '<path d="M3 16v-3l2-5h14l2 5v3"/><path d="M3 16h18v2H3z"/>'
                    '<circle cx="7.5" cy="18.5" r="1.5"/><circle cx="16.5" cy="18.5" r="1.5"/><path d="M5 13h14"/>'),
}

DEFAULT_ICON = "speaker"
EMOJI_TO_ICON = {"🚨": "alarm", "📯": "horn", "⚡": "bolt", "🪙": "coin", "💥": "burst", "👏": "clap",
                 "🔔": "bell", "🤪": "spring", "🥁": "drum", "🚓": "siren", "⏰": "alarm"}


def icon_svg(key: str | None) -> str:
    _, body = ICONS.get(key or "", ICONS[DEFAULT_ICON])
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


LOGO_SVG = ('<svg width="{s}" height="{s}" viewBox="0 0 32 32" aria-hidden="true">'
            '<rect width="32" height="32" rx="9" fill="#ff6a00"/>'
            '<path d="M8 13v6M12 9v14M16 12v8M20 7v18M24 13v6" stroke="#1a1613" stroke-width="2.6" '
            'stroke-linecap="round"/></svg>')


# ================================================================== SONIDOS DE EJEMPLO
# Se sintetizan con numpy, así no dependes de descargar nada.
def _t(sec: float) -> np.ndarray:
    return np.arange(int(SR * sec)) / SR


def _fade(x: np.ndarray, fin: float = 0.005, fout: float = 0.03) -> np.ndarray:
    n = len(x)
    a, r = min(n, int(SR * fin)), min(n, int(SR * fout))
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if r:
        x[-r:] *= np.linspace(1, 0, r)
    return x


def _sine(freqs: np.ndarray) -> np.ndarray:
    return np.sin(2 * np.pi * np.cumsum(freqs) / SR)


def _square(freqs: np.ndarray) -> np.ndarray:
    return np.sign(np.sin(2 * np.pi * np.cumsum(freqs) / SR))


def _saw(freqs: np.ndarray) -> np.ndarray:
    ph = np.cumsum(freqs) / SR
    return 2 * (ph - np.floor(ph + 0.5))


def _noise(n: int, rng) -> np.ndarray:
    return rng.uniform(-1, 1, n)


def snd_alarma():
    parts = []
    for i in range(6):
        f = 880.0 if i % 2 == 0 else 660.0
        beep = _fade(_square(np.full(int(SR * 0.16), f)) * 0.6, 0.003, 0.01)
        parts += [beep, np.zeros(int(SR * 0.04))]
    return np.concatenate(parts)


def snd_bocina():
    t = _t(1.3)
    vib = 1 + 0.006 * np.sin(2 * np.pi * 5.5 * t)
    x = sum(_saw(f * vib) for f in (233.1, 277.2, 349.2, 466.2))
    return _fade(np.tanh(x * 1.5), 0.02, 0.2)


def snd_laser():
    t = _t(0.45)
    f = 1800 * np.exp(-t * 5.5) + 120
    x = 0.6 * _square(f) + 0.4 * _sine(f * 1.01)
    return _fade(x * np.exp(-t * 4), 0.002, 0.03)


def snd_moneda():
    a = _square(np.full(int(SR * 0.08), 988.0)) * 0.5
    t = _t(0.45)
    b = _square(np.full(len(t), 1318.5)) * 0.5 * np.exp(-t * 7)
    return _fade(np.concatenate([a, b]), 0.002, 0.02)


def snd_explosion():
    rng = np.random.default_rng(7)
    t = _t(1.8)
    noise = np.convolve(_noise(len(t), rng), np.ones(24) / 24, mode="same") * 6
    rumble = _sine(60 * np.exp(-t * 1.5) + 30)
    x = (noise * 0.8 + rumble * 0.6) * np.exp(-t * 2.6)
    return _fade(np.tanh(x * 2), 0.01, 0.1)


def snd_aplausos():
    rng = np.random.default_rng(3)
    x = np.zeros(int(SR * 2.4))
    clap = int(SR * 0.03)
    ct = np.arange(clap) / SR
    for _ in range(420):
        pos = rng.integers(0, len(x) - clap)
        x[pos:pos + clap] += _noise(clap, rng) * np.exp(-ct * 120) * rng.uniform(0.3, 1)
    x = np.diff(x, prepend=0)
    idx = np.arange(len(x))
    env = np.minimum(1, idx / (SR * 0.3)) * np.minimum(1, (len(x) - idx) / (SR * 0.6))
    return x * env


def snd_campana():
    t = _t(2.8)
    base = 659.3
    partials = [(1, 1, 1.6), (2.0, 0.6, 2.2), (2.76, 0.5, 2.8), (5.4, 0.3, 4.5), (8.93, 0.15, 6)]
    x = sum(a * np.sin(2 * np.pi * base * r * t) * np.exp(-t * d) for r, a, d in partials)
    return _fade(x, 0.002, 0.1)


def snd_boing():
    t = _t(0.9)
    f = 220 + 180 * np.exp(-t * 3) * np.sin(2 * np.pi * 14 * t) + 80 * t
    x = _sine(f) + 0.3 * _sine(2 * f)
    return _fade(x * np.exp(-t * 3.2), 0.003, 0.05)


def snd_tambor():
    rng = np.random.default_rng(5)
    x = np.zeros(int(SR * 1.3))

    def put(sig, at):
        i = int(SR * at)
        x[i:i + len(sig)] += sig[: len(x) - i]

    kt = _t(0.35)
    kick = _sine(50 + 120 * np.exp(-kt * 30)) * np.exp(-kt * 9)
    st_ = _t(0.25)
    for at in (0, 0.6, 0.75):
        put(kick, at)
    for at in (0.3, 0.9):
        snare = (_noise(len(st_), rng) * 0.7 + 0.4 * np.sin(2 * np.pi * 190 * st_)) * np.exp(-st_ * 18)
        put(snare, at)
    ht = _t(0.05)
    for k in range(8):
        put(np.diff(_noise(len(ht), rng), prepend=0) * np.exp(-ht * 90) * 0.25, k * 0.15)
    return x


def snd_sirena():
    t = _t(2.6)
    f = 750 + 300 * np.sin(2 * np.pi * 0.75 * t - np.pi / 2)
    x = _sine(f) * 0.7 + 0.3 * _sine(2 * f) + 0.15 * _sine(3 * f)
    return _fade(x, 0.05, 0.25)


# archivo, nombre, ícono, color, generador
DEFAULT_SOUNDS = [
    ("alarma", "Alarma", "alarm", COLORS["Rojo"], snd_alarma),
    ("bocina", "Bocina", "horn", COLORS["Naranja"], snd_bocina),
    ("laser", "Láser", "bolt", COLORS["Azul"], snd_laser),
    ("moneda", "Moneda", "coin", COLORS["Amarillo"], snd_moneda),
    ("explosion", "Explosión", "burst", COLORS["Negro"], snd_explosion),
    ("aplausos", "Aplausos", "clap", COLORS["Verde"], snd_aplausos),
    ("campana", "Campana", "bell", COLORS["Morado"], snd_campana),
    ("boing", "Boing", "spring", COLORS["Rosa"], snd_boing),
    ("tambor", "Tambor", "drum", COLORS["Cian"], snd_tambor),
    ("sirena", "Sirena", "siren", COLORS["Índigo"], snd_sirena),
]


def _write_wav(path: Path, data: np.ndarray) -> None:
    data = np.nan_to_num(data)
    peak = float(np.max(np.abs(data))) or 1.0
    pcm = (data / peak * 0.9 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


@st.cache_resource(show_spinner=False)
def ensure_default_sounds() -> bool:
    for folder in (DEFAULT_DIR, USER_SOUNDS_DIR, COMMUNITY_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    for key, *_, gen in DEFAULT_SOUNDS:
        path = DEFAULT_DIR / f"{key}.wav"
        if not path.exists():
            _write_wav(path, gen())
    return True


# ================================================================== BASE DE DATOS
@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _ensure_column(con, table: str, col: str, decl: str) -> bool:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        return True
    return False


@st.cache_resource(show_spinner=False)
def init_db() -> bool:
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                pw_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS buttons(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                icon TEXT DEFAULT 'speaker',
                image_b64 TEXT,
                image_mime TEXT,
                color TEXT NOT NULL DEFAULT '#ff6d00',
                sound_path TEXT NOT NULL,
                start_s REAL NOT NULL DEFAULT 0,
                end_s REAL,
                volume REAL NOT NULL DEFAULT 1.0,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS community(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                icon TEXT DEFAULT 'speaker',
                image_b64 TEXT,
                image_mime TEXT,
                color TEXT NOT NULL,
                sound_path TEXT NOT NULL,
                start_s REAL NOT NULL DEFAULT 0,
                end_s REAL,
                volume REAL NOT NULL DEFAULT 1.0,
                downloads INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS community_saves(
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, item_id)
            );
            """
        )
        # columnas de perfil (sirve también para bases de datos de la versión anterior)
        for col, decl in [("display_name", "TEXT"), ("email", "TEXT"), ("bio", "TEXT DEFAULT ''"),
                          ("avatar_b64", "TEXT"), ("avatar_mime", "TEXT")]:
            _ensure_column(con, "users", col, decl)
        # migración desde la versión con emojis
        if _ensure_column(con, "buttons", "icon", "TEXT DEFAULT 'speaker'"):
            cols = {r[1] for r in con.execute("PRAGMA table_info(buttons)")}
            if "emoji" in cols:
                for emoji, key in EMOJI_TO_ICON.items():
                    con.execute("UPDATE buttons SET icon = ? WHERE emoji = ?", (key, emoji))
    return True


# ------------------------------------------------------------------ usuarios y perfil
def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000).hex()


def email_taken(email: str, exclude_uid: int | None = None) -> bool:
    with db() as con:
        row = con.execute("SELECT 1 FROM users WHERE lower(email) = lower(?) AND id != ?",
                          (email, exclude_uid or -1)).fetchone()
    return row is not None


def create_user(username: str, password: str, email: str | None) -> int | None:
    salt = secrets.token_hex(16)
    try:
        with db() as con:
            cur = con.execute(
                "INSERT INTO users(username, pw_hash, salt, display_name, email, bio) VALUES (?, ?, ?, ?, ?, '')",
                (username, _hash(password, salt), salt, username, email or None),
            )
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    seed_default_buttons(uid)  # cada cuenta nueva parte con los 10 botones de ejemplo
    return uid


def verify_user(login: str, password: str) -> dict | None:
    """Permite entrar con el usuario o con el correo."""
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE username = ? OR lower(email) = lower(?)",
                          (login, login)).fetchone()
    if row and hmac.compare_digest(row["pw_hash"], _hash(password, row["salt"])):
        return {"id": row["id"], "username": row["username"]}
    return None


def get_user(uid: int) -> dict | None:
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def update_profile(uid: int, **fields) -> None:
    allowed = {"display_name", "email", "bio", "avatar_b64", "avatar_mime"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with db() as con:
            con.execute(f"UPDATE users SET {sets} WHERE id = ?", (*fields.values(), uid))


def change_password(uid: int, current: str, new: str) -> bool:
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row or not hmac.compare_digest(row["pw_hash"], _hash(current, row["salt"])):
            return False
        salt = secrets.token_hex(16)
        con.execute("UPDATE users SET pw_hash = ?, salt = ? WHERE id = ?", (_hash(new, salt), salt, uid))
    return True


def user_stats(uid: int) -> dict:
    with db() as con:
        buttons = con.execute("SELECT COUNT(*) FROM buttons WHERE user_id = ?", (uid,)).fetchone()[0]
        published = con.execute("SELECT COUNT(*) FROM community WHERE author_id = ?", (uid,)).fetchone()[0]
        saves = con.execute(
            "SELECT COUNT(*) FROM community_saves s JOIN community c ON c.id = s.item_id WHERE c.author_id = ?",
            (uid,)).fetchone()[0]
    return {"buttons": buttons, "published": published, "saves": saves}


# ------------------------------------------------------------------ botones
BUTTON_FIELDS = {"name", "icon", "image_b64", "image_mime", "color", "sound_path",
                 "start_s", "end_s", "volume", "position"}


def _next_position(con, uid: int) -> int:
    return con.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM buttons WHERE user_id = ?", (uid,)
    ).fetchone()[0]


def seed_default_buttons(uid: int) -> None:
    with db() as con:
        pos = _next_position(con, uid)
        for i, (key, name, icon, color, _) in enumerate(DEFAULT_SOUNDS):
            con.execute(
                "INSERT INTO buttons(user_id, name, icon, color, sound_path, position) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, name, icon, color, f"sounds/default/{key}.wav", pos + i),
            )


def get_buttons(uid: int) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM buttons WHERE user_id = ? ORDER BY position, id", (uid,)).fetchall()
    return [dict(r) for r in rows]


def add_button(uid: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in BUTTON_FIELDS}
    with db() as con:
        fields["position"] = _next_position(con, uid)
        cols = ", ".join(["user_id", *fields])
        marks = ", ".join("?" * (len(fields) + 1))
        con.execute(f"INSERT INTO buttons({cols}) VALUES ({marks})", (uid, *fields.values()))


def update_button(uid: int, bid: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in BUTTON_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE buttons SET {sets} WHERE id = ? AND user_id = ?", (*fields.values(), bid, uid))


def cleanup_sound(sound_path: str) -> None:
    """Borra un archivo subido por un usuario si ya ningún botón lo usa."""
    if not sound_path.startswith("sounds/users/"):
        return  # nunca se borran los sonidos de ejemplo ni los de la comunidad
    with db() as con:
        used = con.execute("SELECT COUNT(*) FROM buttons WHERE sound_path = ?", (sound_path,)).fetchone()[0]
    if not used:
        (BASE_DIR / sound_path).unlink(missing_ok=True)


def delete_button(uid: int, bid: int) -> None:
    with db() as con:
        row = con.execute("SELECT sound_path FROM buttons WHERE id = ? AND user_id = ?", (bid, uid)).fetchone()
        con.execute("DELETE FROM buttons WHERE id = ? AND user_id = ?", (bid, uid))
    if row:
        cleanup_sound(row["sound_path"])


def move_button(uid: int, bid: int, direction: int) -> None:
    ids = [b["id"] for b in get_buttons(uid)]
    i = ids.index(bid)
    j = i + direction
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        with db() as con:
            for pos, x in enumerate(ids):
                con.execute("UPDATE buttons SET position = ? WHERE id = ?", (pos, x))


# ------------------------------------------------------------------ comunidad
def _copy_sound(src_rel: str, dest_dir: Path, rel_prefix: str) -> str:
    src = BASE_DIR / src_rel
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}{src.suffix.lower()}"
    shutil.copyfile(src, dest_dir / name)
    return f"{rel_prefix}/{name}"


def publish_button(uid: int, button: dict, description: str) -> None:
    path = _copy_sound(button["sound_path"], COMMUNITY_DIR, "sounds/community")
    with db() as con:
        con.execute(
            "INSERT INTO community(author_id, name, description, icon, image_b64, image_mime, color, sound_path,"
            " start_s, end_s, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, button["name"], description, button["icon"], button["image_b64"], button["image_mime"],
             button["color"], path, button["start_s"], button["end_s"], button["volume"]),
        )


def list_community(uid: int, query: str, order: str, only_mine: bool) -> list[dict]:
    orders = {
        "Más recientes": "c.id DESC",
        "Más guardados": "saves DESC, c.id DESC",
        "Más descargados": "c.downloads DESC, c.id DESC",
    }
    like = f"%{query.strip()}%"
    sql = (
        "SELECT c.*, u.username, u.display_name, u.avatar_b64, u.avatar_mime, u.bio, "
        " (SELECT COUNT(*) FROM community_saves s WHERE s.item_id = c.id) AS saves, "
        " EXISTS(SELECT 1 FROM community_saves s WHERE s.item_id = c.id AND s.user_id = ?) AS saved_by_me "
        "FROM community c JOIN users u ON u.id = c.author_id "
        "WHERE (c.name LIKE ? OR c.description LIKE ? OR u.username LIKE ? OR u.display_name LIKE ?)"
    )
    params: list = [uid, like, like, like, like]
    if only_mine:
        sql += " AND c.author_id = ?"
        params.append(uid)
    sql += f" ORDER BY {orders.get(order, 'c.id DESC')}"
    with db() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def save_from_community(uid: int, username: str, item: dict) -> None:
    path = _copy_sound(item["sound_path"], USER_SOUNDS_DIR / username.lower(), f"sounds/users/{username.lower()}")
    add_button(uid, name=item["name"], icon=item["icon"], image_b64=item["image_b64"],
               image_mime=item["image_mime"], color=item["color"], sound_path=path,
               start_s=item["start_s"], end_s=item["end_s"], volume=item["volume"])
    with db() as con:
        con.execute("INSERT OR IGNORE INTO community_saves(user_id, item_id) VALUES (?, ?)", (uid, item["id"]))


def count_download(item_id: int) -> None:
    with db() as con:
        con.execute("UPDATE community SET downloads = downloads + 1 WHERE id = ?", (item_id,))


def unpublish(uid: int, item_id: int) -> None:
    with db() as con:
        row = con.execute("SELECT sound_path FROM community WHERE id = ? AND author_id = ?",
                          (item_id, uid)).fetchone()
        con.execute("DELETE FROM community WHERE id = ? AND author_id = ?", (item_id, uid))
    if row:
        (BASE_DIR / row["sound_path"]).unlink(missing_ok=True)


# ================================================================== ARCHIVOS
def get_duration(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    if MutagenFile is not None:
        try:
            m = MutagenFile(str(path))
            if m is not None and m.info and m.info.length:
                return float(m.info.length)
        except Exception:
            pass
    return None


def duration_from_bytes(data: bytes, ext: str) -> float | None:
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(data)
        p = Path(tmp.name)
    try:
        return get_duration(p)
    finally:
        p.unlink(missing_ok=True)


def save_uploaded_sound(username: str, uploaded) -> str:
    ext = Path(uploaded.name).suffix.lower()
    folder = USER_SOUNDS_DIR / username.lower()
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:12]}{ext}"
    (folder / fname).write_bytes(uploaded.getvalue())
    return f"sounds/users/{username.lower()}/{fname}"


@st.cache_data(show_spinner=False, max_entries=500)
def file_b64(path_str: str, mtime: float) -> str:
    return base64.b64encode(Path(path_str).read_bytes()).decode()


def image_to_b64(uploaded, max_px: int = 256, square: bool = False) -> tuple[str | None, str | None]:
    """Reduce la imagen a max_px para que la base de datos no crezca de más."""
    if uploaded is None:
        return None, None
    data = uploaded.getvalue()
    if Image is not None:
        try:
            im = Image.open(io.BytesIO(data))
            if not getattr(im, "is_animated", False):  # los GIF animados se dejan tal cual
                im = ImageOps.exif_transpose(im)
                im = ImageOps.fit(im, (max_px, max_px)) if square else im
                im.thumbnail((max_px, max_px))
                out = io.BytesIO()
                if im.mode in ("RGBA", "LA", "P"):
                    im.convert("RGBA").save(out, "PNG")
                    mime = "image/png"
                else:
                    im.convert("RGB").save(out, "JPEG", quality=88)
                    mime = "image/jpeg"
                return base64.b64encode(out.getvalue()).decode(), mime
        except Exception:
            pass
    return base64.b64encode(data).decode(), (uploaded.type or "image/png")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "sonido"


def esc(text: str | None) -> str:
    return html.escape(text or "")


# ================================================================== PAD (HTML + Web Audio)
PAD_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--line:#2b2420;--orange:#ff6a00;--orange2:#ffa04d;--text:#f4efe9;--muted:#9a8f86;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:transparent;color:var(--text);font-family:Outfit,system-ui,sans-serif;user-select:none;-webkit-user-select:none;padding:4px 4px 10px}
.bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;background:#1a1613;border:1px solid #3a2414;
 border-radius:16px;padding:11px 14px;margin-bottom:16px;position:sticky;top:0;z-index:5}
.live{display:flex;align-items:center;gap:9px;font-weight:600;font-size:15px;min-width:110px}
.dot{width:10px;height:10px;border-radius:50%;background:#3d332c;transition:.2s}
.dot.on{background:var(--orange);box-shadow:0 0 12px var(--orange)}
.vol{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px}
input[type=range]{accent-color:var(--orange);width:140px}
.spacer{flex:1}
.hint{color:var(--muted);font-size:13px}
.stop{display:inline-flex;align-items:center;gap:8px;background:#e5322b;color:#fff;border:none;border-radius:11px;
 padding:9px 16px;font:600 14px Outfit,sans-serif;cursor:pointer;transition:transform .08s,background .15s}
.stop:hover{background:#ff3b30}.stop:active{transform:scale(.95)}
.stop svg{width:16px;height:16px}
.stop:focus-visible,.pad:focus-visible{outline:3px solid var(--orange2);outline-offset:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:14px}
.pad{--c:#ff6a00;position:relative;aspect-ratio:1/1;border-radius:22px;cursor:pointer;overflow:hidden;
 border:1px solid rgba(255,255,255,.07);
 background:linear-gradient(160deg, color-mix(in srgb,var(--c) 88%,#fff 12%), color-mix(in srgb,var(--c) 68%,#000));
 box-shadow:0 8px 18px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.2);
 display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;
 transition:transform .06s ease, box-shadow .2s}
.pad:hover{box-shadow:0 0 0 2px var(--orange), 0 10px 24px rgba(0,0,0,.5)}
.pad.hit{transform:scale(.93) translateY(2px)}
.pad.playing{box-shadow:0 0 0 2px #fff, 0 0 26px color-mix(in srgb,var(--c) 70%,#fff)}
.pad.missing{filter:grayscale(.8);opacity:.5}
.icon{width:48%;aspect-ratio:1/1;border-radius:50%;background:rgba(0,0,0,.17);display:flex;align-items:center;
 justify-content:center;overflow:hidden;color:#fff;pointer-events:none}
.icon svg{width:52%;height:52%;filter:drop-shadow(0 1px 1px rgba(0,0,0,.25))}
.icon img{width:100%;height:100%;object-fit:cover}
.name{font-weight:600;font-size:15px;text-shadow:0 1px 3px rgba(0,0,0,.5);max-width:90%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.key{position:absolute;top:9px;left:10px;font-size:11px;font-weight:600;background:rgba(0,0,0,.28);padding:1px 7px;border-radius:7px;color:rgba(255,255,255,.85)}
.count{position:absolute;top:9px;right:10px;font-size:12px;font-weight:700;background:#fff;color:#1a1613;padding:1px 8px;border-radius:7px;opacity:0;transform:scale(.5);transition:.12s}
.count.on{opacity:1;transform:scale(1)}
.prog{position:absolute;left:0;bottom:0;height:4px;width:0;background:rgba(255,255,255,.9)}
.ripple{position:absolute;width:60px;height:60px;margin:-30px 0 0 -30px;border-radius:50%;background:rgba(255,255,255,.45);transform:scale(0);animation:rip .45s ease-out forwards;pointer-events:none}
@keyframes rip{to{transform:scale(5);opacity:0}}
.empty{border:2px dashed #3a2a20;border-radius:20px;padding:40px;text-align:center;color:var(--muted);font-size:16px}
.preview{display:grid;grid-template-columns:170px 1fr;gap:18px;align-items:center}
.wave{width:100%;height:120px;border-radius:14px;background:#161210;border:1px solid var(--line);display:block}
.pinfo{display:flex;align-items:center;gap:12px;margin-top:10px;color:var(--muted);font-size:14px;flex-wrap:wrap}
.pinfo .stop{padding:7px 13px;font-size:13px}
.mini .pad{aspect-ratio:auto;height:92px;flex-direction:row;justify-content:flex-start;gap:14px;padding:0 16px;border-radius:18px}
.mini .icon{width:58px;flex:none}
.mini .name{flex:1;text-align:left;font-size:17px;max-width:none}
.mini .pl{width:30px;height:30px;border-radius:50%;background:rgba(255,255,255,.92);color:#1a1613;display:flex;align-items:center;justify-content:center;flex:none;pointer-events:none}
.mini .pl svg{width:14px;height:14px;margin-left:2px}
@media (max-width:560px){.grid{grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:10px}.name{font-size:13px}
 .preview{grid-template-columns:1fr}.hint{display:none}input[type=range]{width:100px}}
@media (prefers-reduced-motion:reduce){.ripple{display:none}.pad{transition:none}}
</style></head>
<body><div id="root"></div>
<!-- __NONCE__ -->
<script>
const DATA = __DATA__;
const MODE = "__MODE__";
const KEYS = "1234567890QWERTYUIOPASDFGHJKLZXCVBNM";
const STOP_SVG = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
const PLAY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>';
const AC = window.AudioContext || window.webkitAudioContext;
const ctx = new AC();
const master = ctx.createGain(); master.gain.value = 0.9; master.connect(ctx.destination);
const buffers = {}, perPad = {}, active = new Set();
const pads = [];

function b64ToBuf(b64){const bin=atob(b64);const u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return u.buffer;}
DATA.forEach(b=>{
  buffers[b.id] = b.audio
    ? new Promise(res=>{ctx.decodeAudioData(b64ToBuf(b.audio), res, ()=>res(null));}).catch(()=>null)
    : Promise.resolve(null);
});

const root = document.getElementById('root');
if (MODE === 'pad') {
  root.innerHTML = `
    <div class="bar">
      <div class="live"><span class="dot" id="dot"></span><span id="cnt">0 sonando</span></div>
      <label class="vol">Volumen <input type="range" id="vol" min="0" max="1.5" step="0.01" value="0.9"></label>
      <span class="spacer"></span>
      <span class="hint">Teclas 1–0, Q–P… · Esc detiene todo</span>
      <button class="stop" id="stopBtn">${STOP_SVG}Detener todos los sonidos</button>
    </div>
    <div class="grid" id="grid"></div>`;
  document.getElementById('vol').addEventListener('input', e => master.gain.value = parseFloat(e.target.value));
  const grid = document.getElementById('grid');
  if (!DATA.length) grid.outerHTML = '<div class="empty">Tu set está vacío. Abre <b>Agregar botón</b> o guarda alguno desde <b>Comunidad</b>.</div>';
  DATA.forEach((b,i) => { const el = makePad(b,i); pads.push(el); grid.appendChild(el); });
} else if (MODE === 'mini') {
  root.innerHTML = '<div class="mini" id="one"></div>';
  if (DATA.length) { const el = makePad(DATA[0], -1); pads.push(el); document.getElementById('one').appendChild(el); }
} else {
  root.innerHTML = `
    <div class="preview">
      <div id="one"></div>
      <div><canvas class="wave" id="wave"></canvas>
        <div class="pinfo"><span id="info">Cargando audio…</span><span class="spacer"></span>
        <button class="stop" id="stopBtn">${STOP_SVG}Detener</button></div></div>
    </div>`;
  if (DATA.length) { const el = makePad(DATA[0], -1); pads.push(el); document.getElementById('one').appendChild(el); drawWave(DATA[0]); }
}
const sb = document.getElementById('stopBtn'); if (sb) sb.addEventListener('click', stopAll);

function makePad(b, i){
  const el = document.createElement('div');
  el.className = 'pad' + (b.audio ? '' : ' missing');
  el.tabIndex = 0;
  el.style.setProperty('--c', b.color);
  el.innerHTML = (i >= 0 && i < KEYS.length ? `<span class="key">${KEYS[i]}</span>` : '') +
    '<span class="count"></span><div class="icon"></div><div class="name"></div><div class="prog"></div>' +
    (MODE === 'mini' ? `<div class="pl">${PLAY_SVG}</div>` : '');
  const icon = el.querySelector('.icon');
  if (b.img) { const im = document.createElement('img'); im.src = b.img; im.alt = ''; icon.appendChild(im); }
  else icon.innerHTML = b.svg;   // dibujo SVG generado por la app (no por el usuario)
  el.querySelector('.name').textContent = b.name;
  el.title = b.audio ? b.name : 'No se encontró el archivo de sonido';
  el.addEventListener('pointerdown', ev => { ev.preventDefault(); play(b, el, ev); });
  el.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.stopPropagation(); play(b, el); } });
  return el;
}

function hit(el, ev){
  el.classList.add('hit'); setTimeout(()=>el.classList.remove('hit'), 90);
  const r = el.getBoundingClientRect(), rip = document.createElement('span');
  rip.className = 'ripple';
  rip.style.left = (ev && ev.clientX ? ev.clientX - r.left : r.width/2) + 'px';
  rip.style.top  = (ev && ev.clientY ? ev.clientY - r.top  : r.height/2) + 'px';
  el.appendChild(rip); setTimeout(()=>rip.remove(), 460);
}
function updateGlobal(){
  const n = active.size, c = document.getElementById('cnt'), d = document.getElementById('dot');
  if (c) c.textContent = n + ' sonando';
  if (d) d.classList.toggle('on', n > 0);
}
function refreshPad(id, el){
  const n = perPad[id] || 0, badge = el.querySelector('.count');
  el.classList.toggle('playing', n > 0);
  badge.textContent = 'x' + n; badge.classList.toggle('on', n > 1);
  updateGlobal();
}
function animateProg(el, dur){
  const p = el.querySelector('.prog');
  p.style.transition = 'none'; p.style.width = '0'; void p.offsetWidth;
  p.style.transition = `width ${dur}s linear`; p.style.width = '100%';
  clearTimeout(p._t); p._t = setTimeout(()=>{p.style.transition='none'; p.style.width='0';}, dur*1000 + 60);
}

async function play(b, el, ev){
  if (ctx.state !== 'running') { try { ctx.resume(); } catch(e){} }
  hit(el, ev);
  const buf = await buffers[b.id];
  if (!buf) return;
  const s = Math.min(Math.max(b.start || 0, 0), buf.duration);
  const e = (b.end && b.end > s) ? Math.min(b.end, buf.duration) : buf.duration;
  const dur = Math.max(0.02, e - s);
  const src = ctx.createBufferSource(); src.buffer = buf;
  const g = ctx.createGain(); const now = ctx.currentTime; const v = (b.volume ?? 1);
  g.gain.setValueAtTime(v, now);
  if (dur > 0.08) { g.gain.setValueAtTime(v, now + dur - 0.03); g.gain.linearRampToValueAtTime(0.0001, now + dur); }
  src.connect(g); g.connect(master);
  src.start(now, s, dur);                       // cada clic = una voz nueva -> se acumulan sin límite
  active.add(src); perPad[b.id] = (perPad[b.id] || 0) + 1; refreshPad(b.id, el);
  animateProg(el, dur);
  src.onended = () => {
    active.delete(src); perPad[b.id] = Math.max(0, (perPad[b.id] || 1) - 1);
    refreshPad(b.id, el); try { g.disconnect(); } catch(e){}
  };
}

function stopAll(){
  active.forEach(s => { try { s.stop(); } catch(e){} });
  active.clear();
  for (const k in perPad) perPad[k] = 0;
  pads.forEach(p => { p.classList.remove('playing'); p.querySelector('.count').classList.remove('on');
    const pr = p.querySelector('.prog'); pr.style.transition = 'none'; pr.style.width = '0'; });
  updateGlobal();
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { stopAll(); return; }
  if (MODE !== 'pad' || e.ctrlKey || e.metaKey || e.altKey) return;
  const i = KEYS.indexOf((e.key || '').toUpperCase());
  if (i >= 0 && i < DATA.length) { e.preventDefault(); play(DATA[i], pads[i]); }  // mantener apretada = ráfaga
});

async function drawWave(b){
  const buf = await buffers[b.id], cv = document.getElementById('wave'), info = document.getElementById('info');
  if (!buf) { info.textContent = 'No se pudo leer este audio. Prueba con otro archivo MP3 o WAV.'; return; }
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const c = cv.getContext('2d'); c.scale(dpr, dpr);
  const data = buf.getChannelData(0), step = Math.max(1, Math.floor(data.length / w)), mid = h / 2;
  const s = Math.min(b.start || 0, buf.duration);
  const e = (b.end && b.end > s) ? Math.min(b.end, buf.duration) : buf.duration;
  const xs = s / buf.duration * w, xe = e / buf.duration * w;
  c.fillStyle = 'rgba(255,106,0,.12)'; c.fillRect(xs, 0, xe - xs, h);
  for (let x = 0; x < w; x++) {
    let mn = 1, mx = -1; const off = x * step;
    for (let j = 0; j < step; j++) { const v = data[off + j]; if (v === undefined) break; if (v < mn) mn = v; if (v > mx) mx = v; }
    if (mx < mn) continue;
    c.fillStyle = (x >= xs && x <= xe) ? '#ff8a2a' : '#4a3f38';
    c.fillRect(x, mid + mn * mid * 0.92, 1, Math.max(1, (mx - mn) * mid * 0.92));
  }
  c.fillStyle = '#ffd2a8'; c.fillRect(xs - 1, 0, 2, h); c.fillRect(xe - 1, 0, 2, h);
  info.textContent = `Audio completo: ${buf.duration.toFixed(2)} s · Se reproduce de ${s.toFixed(2)} s a ${e.toFixed(2)} s (${(e - s).toFixed(2)} s)`;
}
</script></body></html>"""


def button_payload(b: dict, audio_b64: str | None = None) -> dict:
    if audio_b64 is None:
        path = BASE_DIR / b["sound_path"]
        audio_b64 = file_b64(str(path), path.stat().st_mtime) if path.exists() else None
    img = f"data:{b['image_mime']};base64,{b['image_b64']}" if b.get("image_b64") else None
    end = b.get("end_s")
    return {
        "id": b.get("id", 0),
        "name": b.get("name") or "Sin nombre",
        "svg": icon_svg(b.get("icon")),
        "img": img,
        "color": b.get("color") or COLORS["Naranja"],
        "start": float(b.get("start_s") or 0),
        "end": float(end) if end else None,
        "volume": float(b.get("volume") if b.get("volume") is not None else 1),
        "audio": audio_b64,
    }


def build_pad_html(payload: list[dict], mode: str = "pad", nonce: int = 0) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return (PAD_TEMPLATE.replace("__DATA__", data)
            .replace("__MODE__", mode)
            .replace("__NONCE__", f"stop-{nonce}"))


def show_html(content: str, height: int) -> None:
    """Usa st.iframe en Streamlit nuevo y components.html en versiones anteriores."""
    if hasattr(st, "iframe"):
        st.iframe(content, height="content")
    else:
        components.html(content, height=height, scrolling=True)


def render_preview(b: dict, audio_b64: str | None) -> None:
    show_html(build_pad_html([button_payload(b, audio_b64)], "preview", st.session_state.stop_nonce), 215)


def icon_gallery_html(selected: str, color: str) -> str:
    cells = "".join(
        f'<div class="ic{" sel" if k == selected else ""}"><div class="sw">{icon_svg(k)}</div>'
        f'<span>{esc(label)}</span></div>'
        for k, (label, _) in ICONS.items()
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
        "body{margin:0;padding:4px;font-family:Outfit,system-ui,sans-serif;background:transparent}"
        ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(74px,1fr));gap:10px}"
        ".ic{display:flex;flex-direction:column;align-items:center;gap:5px;font-size:11px;color:#9a8f86}"
        f".sw{{width:44px;height:44px;border-radius:13px;background:{color};color:#fff;display:flex;"
        "align-items:center;justify-content:center}"
        ".sw svg{width:24px;height:24px}.sel{color:#fff}.sel .sw{outline:2px solid #fff;outline-offset:2px}"
        f"</style></head><body><div class='g'>{cells}</div></body></html>"
    )


# ================================================================== ESTILO DE LA APP
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap');
.stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp li {
  font-family: 'Outfit', system-ui, sans-serif;
}
.stApp { background: radial-gradient(1100px 520px at 12% -12%, #3a1a06 0%, #0f0d0c 58%) fixed; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: #15110f; border-right: 1px solid #2b2420; }
.block-container { padding-top: 2.2rem; max-width: 1320px; }
.stApp h2 { font-weight: 700; letter-spacing: -.5px; }
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button, [data-testid="stPopover"] button {
  border-radius: 12px; font-weight: 500; border: 1px solid #3a2e27; transition: .15s;
}
.stButton > button:hover, .stFormSubmitButton > button:hover, .stDownloadButton > button:hover {
  border-color: #ff6a00; color: #ffa04d;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: #ff6a00; border: none; color: #fff;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover { background: #ff7d1f; color: #fff; }
.st-key-stop_all button { background: #e5322b !important; color: #fff !important; border: none !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 18px; }
.ss-logo { display: flex; align-items: center; gap: .55rem; font-weight: 700; letter-spacing: -1px; line-height: 1.1; color: #f4efe9; }
.ss-logo b { color: #ff7a1a; font-weight: 700; }
.ss-sub { color: #9a8f86; margin: .3rem 0 1.2rem; }
.ss-muted { color: #9a8f86; font-size: .92rem; }
.ss-me { display: flex; align-items: center; gap: 12px; }
.ss-me-name { font-weight: 600; font-size: 1.05rem; color: #f4efe9; line-height: 1.2; }
.ss-avatar { border-radius: 50%; object-fit: cover; flex: none; display: inline-flex; align-items: center;
  justify-content: center; background: #ff6a00; color: #1a1613; font-weight: 700; }
.ss-profile { display: flex; flex-direction: column; align-items: center; text-align: center; gap: .35rem; padding: .6rem 0; }
.ss-profile h3 { margin: .4rem 0 0; padding: 0; }
.ss-bio { color: #d9cfc6; margin-top: .5rem; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.ss-stats { display: flex; gap: 1.6rem; justify-content: center; margin-top: .9rem; }
.ss-stats div { display: flex; flex-direction: column; }
.ss-stats b { font-size: 1.35rem; color: #ff8a2a; }
.ss-stats span { color: #9a8f86; font-size: .82rem; }
.ss-author { display: flex; align-items: center; gap: 8px; color: #d9cfc6; font-size: .92rem; margin: .1rem 0 .2rem; }
div[data-baseweb="tab-list"] button[aria-selected="true"] p { color: #ffa04d; }
/* modo edición del SoundPad */
.ss-tile { --c: #ff6a00; aspect-ratio: 1 / 1; border-radius: 22px; border: 1px solid rgba(255,255,255,.07);
  background: linear-gradient(160deg, color-mix(in srgb, var(--c) 88%, #fff 12%), color-mix(in srgb, var(--c) 68%, #000));
  box-shadow: 0 8px 18px rgba(0,0,0,.42), inset 0 1px 0 rgba(255,255,255,.2);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px;
  animation: ss-wiggle 1.6s ease-in-out infinite; margin-bottom: .2rem; }
.ss-tile:nth-child(odd) { animation-delay: -.8s; }
.ss-tile .ic { width: 48%; aspect-ratio: 1 / 1; border-radius: 50%; background: rgba(0,0,0,.17); display: flex;
  align-items: center; justify-content: center; color: #fff; overflow: hidden; }
.ss-tile .ic svg { width: 52%; height: 52%; }
.ss-tile .ic img { width: 100%; height: 100%; object-fit: cover; }
.ss-tile .nm { font-weight: 600; color: #fff; text-shadow: 0 1px 3px rgba(0,0,0,.5); max-width: 90%;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ss-tile.add { background: transparent; border: 2px dashed #4a3a30; box-shadow: none; animation: none; color: #9a8f86; }
.ss-tile.add svg { width: 34%; height: 34%; }
@keyframes ss-wiggle { 0%, 100% { transform: rotate(-.6deg); } 50% { transform: rotate(.6deg); } }
@media (prefers-reduced-motion: reduce) { .ss-tile { animation: none; } }
[class*="st-key-del_"] button { border-color: #5a1f1c !important; color: #ff5a52 !important; }
[class*="st-key-del_"] button:hover { background: #e5322b !important; border-color: #e5322b !important; color: #fff !important; }
.st-key-dlg_del button { background: #e5322b !important; border: none !important; color: #fff !important; }
#MainMenu, footer { visibility: hidden; }
</style>
"""


def logo(size: int = 30) -> None:
    st.markdown(f'<div class="ss-logo" style="font-size:{size}px">{LOGO_SVG.format(s=size + 4)}'
                f'<span>Sound<b>Set</b></span></div>', unsafe_allow_html=True)


def avatar_html(u: dict, size: int = 40) -> str:
    if u.get("avatar_b64"):
        return (f'<img class="ss-avatar" src="data:{esc(u.get("avatar_mime"))};base64,{u["avatar_b64"]}" '
                f'style="width:{size}px;height:{size}px" alt="">')
    name = (u.get("display_name") or u.get("username") or "?").strip()
    initials = "".join(p[0] for p in name.split()[:2]).upper() or "?"
    return (f'<span class="ss-avatar" style="width:{size}px;height:{size}px;font-size:{size * 0.4:.0f}px">'
            f'{esc(initials)}</span>')


def flash(msg: str, icon: str = ":material/check_circle:") -> None:
    st.session_state.flash = (msg, icon)


def show_flash() -> None:
    if "flash" in st.session_state:
        msg, icon = st.session_state.pop("flash")
        st.toast(msg, icon=icon)


def color_selector(prefix: str, current: str) -> str:
    names = list(COLORS) + ["Personalizado"]
    cur = next((k for k, v in COLORS.items() if v.lower() == (current or "").lower()), "Personalizado")
    choice = st.selectbox("Color del botón", names, index=names.index(cur), key=f"{prefix}_colsel")
    if choice == "Personalizado":
        return st.color_picker("Elige tu color", current or "#ff6d00", key=f"{prefix}_colpick")
    return COLORS[choice]


def icon_selector(prefix: str, current: str | None, color: str) -> str:
    keys = list(ICONS)
    idx = keys.index(current) if current in ICONS else keys.index(DEFAULT_ICON)
    key = st.selectbox("Ícono", keys, index=idx, format_func=lambda k: ICONS[k][0], key=f"{prefix}_icon",
                       help="Si subes una imagen, se muestra la imagen en vez del ícono.")
    with st.expander("Ver todos los íconos", icon=":material/apps:"):
        show_html(icon_gallery_html(key, color), 330)
    return key


# ================================================================== PÁGINAS
def page_auth() -> None:
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.markdown("<div style='height:7vh'></div>", unsafe_allow_html=True)
        logo(44)
        st.markdown('<p class="ss-sub">Tu SoundPad en la web. Arma tu set, compártelo y descubre los de la comunidad.</p>',
                    unsafe_allow_html=True)
        t_login, t_reg = st.tabs(["Iniciar sesión", "Crear cuenta"])

        with t_login:
            with st.form("login_form"):
                u = st.text_input("Usuario o correo")
                p = st.text_input("Contraseña", type="password")
                ok = st.form_submit_button("Entrar", type="primary", width="stretch")
            if ok:
                user = verify_user(u.strip(), p)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("Usuario, correo o contraseña incorrectos.")

        with t_reg:
            with st.form("register_form"):
                u = st.text_input("Usuario", help="3 a 20 caracteres: letras, números, _ o -")
                email = st.text_input("Correo (opcional)")
                p = st.text_input("Contraseña", type="password", help="Mínimo 4 caracteres")
                p2 = st.text_input("Repite la contraseña", type="password")
                ok = st.form_submit_button("Crear cuenta", type="primary", width="stretch")
            if ok:
                u, email = u.strip(), email.strip()
                if not USERNAME_RE.match(u):
                    st.error("El usuario debe tener de 3 a 20 caracteres (letras, números, _ o -).")
                elif email and not EMAIL_RE.match(email):
                    st.error("Ese correo no parece válido. Revisa que tenga @ y un dominio.")
                elif email and email_taken(email):
                    st.error("Ese correo ya está registrado en otra cuenta.")
                elif len(p) < 4:
                    st.error("La contraseña debe tener al menos 4 caracteres.")
                elif p != p2:
                    st.error("Las contraseñas no coinciden.")
                else:
                    uid = create_user(u, p, email)
                    if uid is None:
                        st.error("Ese usuario ya existe. Elige otro nombre.")
                    else:
                        st.session_state.user = {"id": uid, "username": u}
                        flash(f"Cuenta creada. Bienvenido/a, {u}: te dejamos 10 botones de ejemplo.",
                              ":material/celebration:")
                        st.rerun()


def sidebar(me: dict, n_buttons: int) -> str:
    with st.sidebar:
        logo(26)
        st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="ss-me">{avatar_html(me, 44)}<div><div class="ss-me-name">'
            f'{esc(me.get("display_name") or me["username"])}</div>'
            f'<div class="ss-muted">@{esc(me["username"])} · {n_buttons} botones</div></div></div>',
            unsafe_allow_html=True,
        )
        st.divider()
        page = st.radio("Menú", PAGES, key="page", label_visibility="collapsed")
        st.divider()
        if st.button("Detener todos los sonidos", key="stop_all", icon=":material/stop_circle:", width="stretch"):
            st.session_state.stop_nonce += 1
        st.caption("Dentro del pad también puedes presionar **Esc**.")
        st.divider()
        if st.button("Cerrar sesión", icon=":material/logout:", width="stretch"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()
    return page


def go_to(page: str) -> None:
    """Cambia de sección desde un botón (se usa como callback, antes de dibujar el menú)."""
    st.session_state.page = page


def page_header(title: str, target: str, label: str, icon: str) -> None:
    """Título de la página con un acceso rápido a otra sección a la derecha."""
    t, b = st.columns([4, 1.2], vertical_alignment="bottom")
    t.markdown(f"## {title}")
    b.button(label, icon=icon, on_click=go_to, args=(target,), width="stretch", key=f"go_{title}")


def open_editor(bid: int) -> None:
    """Lápiz del modo edición: abre Editar botones con ese botón ya elegido."""
    st.session_state.page = PAGE_EDIT
    st.session_state.edit_sel = bid


@st.dialog("Eliminar botón")
def confirm_delete(uid: int, b: dict) -> None:
    st.markdown(f"¿Quieres eliminar **{esc(b['name'])}** de tu SoundPad? Esta acción no se puede deshacer.")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", key="dlg_cancel", width="stretch"):
        st.rerun()
    if c2.button("Eliminar", key="dlg_del", icon=":material/delete:", width="stretch"):
        delete_button(uid, b["id"])
        st.session_state.pop("edit_sel", None)
        flash(f"Botón «{b['name']}» eliminado.", ":material/delete:")
        st.rerun()


def tile_html(b: dict) -> str:
    if b.get("image_b64"):
        inner = f'<img src="data:{esc(b.get("image_mime"))};base64,{b["image_b64"]}" alt="">'
    else:
        inner = icon_svg(b.get("icon"))
    return (f'<div class="ss-tile" style="--c:{esc(b.get("color"))}"><div class="ic">{inner}</div>'
            f'<div class="nm">{esc(b["name"])}</div></div>')


ADD_TILE = ('<div class="ss-tile add"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg><div>Nuevo botón</div></div>')


def render_edit_grid(me: dict, buttons: list[dict], per_row: int = 5) -> None:
    st.markdown('<p class="ss-muted">Toca el lápiz para editar un botón o el basurero para eliminarlo. '
                'Desactiva el modo edición para volver a tocar los sonidos.</p>', unsafe_allow_html=True)
    items: list[dict | None] = [*buttons, None]  # None = casilla para agregar un botón nuevo
    for r in range(0, len(items), per_row):
        cols = st.columns(per_row, gap="small")
        for col, b in zip(cols, items[r:r + per_row]):
            with col:
                if b is None:
                    st.markdown(ADD_TILE, unsafe_allow_html=True)
                    st.button("Agregar", key="tile_add", icon=":material/add:", on_click=go_to,
                              args=(PAGE_ADD,), width="stretch")
                    continue
                st.markdown(tile_html(b), unsafe_allow_html=True)
                left, right = st.columns(2, gap="small")
                left.button("", key=f"edit_{b['id']}", icon=":material/edit:", help="Editar",
                            on_click=open_editor, args=(b["id"],), width="stretch")
                if right.button("", key=f"del_{b['id']}", icon=":material/delete:", help="Eliminar",
                                width="stretch"):
                    confirm_delete(me["id"], b)


def page_pad(me: dict, buttons: list[dict]) -> None:
    t, a, e, c = st.columns([3, 1.3, 1.3, 1.2], vertical_alignment="bottom")
    t.markdown("## Mi SoundPad")
    a.button("Agregar botón", key="pad_add", icon=":material/add:", type="primary", on_click=go_to,
             args=(PAGE_ADD,), width="stretch")
    edit_mode = e.toggle("Modo edición", key="edit_mode")
    c.button("Comunidad", key="pad_com", icon=":material/groups:", on_click=go_to, args=(PAGE_COMMUNITY,),
             width="stretch")

    if edit_mode:
        render_edit_grid(me, buttons)
        return

    st.markdown('<p class="ss-muted">Aprieta los botones todas las veces que quieras: los sonidos se acumulan.</p>',
                unsafe_allow_html=True)
    payload = [button_payload(b) for b in buttons]
    rows = max(1, math.ceil(len(buttons) / 5))
    show_html(build_pad_html(payload, "pad", st.session_state.stop_nonce), 110 + rows * 185)


def page_add(user: dict) -> None:
    v = st.session_state.setdefault("add_ver", 0)
    page_header("Agregar botón", PAGE_PAD, "Mi SoundPad", ":material/grid_view:")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        name = st.text_input("Nombre del botón", max_chars=24, key=f"add_name_{v}", placeholder="Ej: Risa malvada")
        color = color_selector(f"add_{v}", COLORS["Naranja"])
        icon = icon_selector(f"add_{v}", DEFAULT_ICON, color)
        img = st.file_uploader("Imagen propia (opcional, reemplaza al ícono)",
                               type=["png", "jpg", "jpeg", "webp", "gif"], key=f"add_img_{v}")
    with c2:
        snd = st.file_uploader("Sonido (MP3 o WAV)", type=["mp3", "wav"], key=f"add_snd_{v}")
        volume = st.slider("Volumen del botón", 0.0, 2.0, 1.0, 0.05, key=f"add_vol_{v}")
        start, end, dur, audio_b64 = 0.0, None, None, None
        if snd is not None:
            data = snd.getvalue()
            audio_b64 = base64.b64encode(data).decode()
            dur = round(duration_from_bytes(data, Path(snd.name).suffix.lower()) or 30.0, 2)
            start, end = st.slider("Recorte: inicio y fin (segundos)", 0.0, dur, (0.0, dur), step=0.01,
                                   key=f"add_trim_{v}_{snd.name}_{snd.size}")

    if snd is not None:
        st.markdown("#### Vista previa")
        img_b64, img_mime = image_to_b64(img)
        render_preview({"name": name or "Nuevo botón", "icon": icon, "color": color, "image_b64": img_b64,
                        "image_mime": img_mime, "start_s": start, "end_s": end, "volume": volume}, audio_b64)

    if st.button("Guardar botón", type="primary", icon=":material/save:"):
        if not name.strip():
            st.error("Escribe un nombre para el botón.")
        elif snd is None:
            st.error("Sube un archivo MP3 o WAV para el botón.")
        else:
            img_b64, img_mime = image_to_b64(img)
            add_button(
                user["id"], name=name.strip(), icon=icon, image_b64=img_b64, image_mime=img_mime, color=color,
                sound_path=save_uploaded_sound(user["username"], snd), start_s=float(start),
                end_s=None if (end is None or end >= dur - 0.01) else float(end), volume=float(volume),
            )
            st.session_state.add_ver = v + 1
            flash(f"Botón «{name.strip()}» guardado.")
            st.rerun()


def page_edit(user: dict, buttons: list[dict]) -> None:
    v = st.session_state.setdefault("edit_ver", 0)
    page_header("Editar botones", PAGE_PAD, "Mi SoundPad", ":material/grid_view:")
    if not buttons:
        st.info("Tu set está vacío. Agrega un botón nuevo, guarda uno de la comunidad o recupera los de ejemplo.")
        if st.button("Recuperar los 10 botones de ejemplo", type="primary", icon=":material/restore:"):
            seed_default_buttons(user["id"])
            flash("Botones de ejemplo agregados.")
            st.rerun()
        return

    labels = {b["id"]: f"{i + 1}. {b['name']}" for i, b in enumerate(buttons)}
    bid = st.selectbox("Botón a editar", list(labels), format_func=labels.get, key="edit_sel")
    b = next(x for x in buttons if x["id"] == bid)
    k = f"e{bid}_{v}"

    c1, c2 = st.columns(2, gap="large")
    with c1:
        name = st.text_input("Nombre", b["name"], max_chars=24, key=f"{k}_name")
        color = color_selector(k, b["color"])
        icon = icon_selector(k, b["icon"], color)
        remove_img = False
        if b["image_b64"]:
            ic, cc = st.columns([1, 4])
            ic.image(base64.b64decode(b["image_b64"]), width=64)
            remove_img = cc.checkbox("Quitar imagen y usar el ícono", key=f"{k}_rmimg")
        new_img = st.file_uploader("Imagen propia (opcional, reemplaza al ícono)",
                                   type=["png", "jpg", "jpeg", "webp", "gif"], key=f"{k}_img")
    with c2:
        new_snd = st.file_uploader("Reemplazar sonido (opcional)", type=["mp3", "wav"], key=f"{k}_snd")
        if new_snd is not None:
            data = new_snd.getvalue()
            audio_b64 = base64.b64encode(data).decode()
            dur = duration_from_bytes(data, Path(new_snd.name).suffix.lower()) or 30.0
            default = (0.0, round(dur, 2))
            trim_key = f"{k}_trim_{new_snd.name}_{new_snd.size}"
        else:
            path = BASE_DIR / b["sound_path"]
            audio_b64 = file_b64(str(path), path.stat().st_mtime) if path.exists() else None
            if audio_b64 is None:
                st.warning("No se encontró el archivo de este sonido. Sube uno nuevo para reemplazarlo.")
            dur = get_duration(path) or 30.0
            default = (min(float(b["start_s"] or 0), round(dur, 2)), min(float(b["end_s"] or dur), round(dur, 2)))
            trim_key = f"{k}_trim_orig"
        dur = round(dur, 2)
        start, end = st.slider("Recorte: inicio y fin (segundos)", 0.0, dur, default, step=0.01, key=trim_key)
        volume = st.slider("Volumen del botón", 0.0, 2.0, float(b["volume"]), 0.05, key=f"{k}_vol")

    if new_img is not None:
        img_b64, img_mime = image_to_b64(new_img)
    elif remove_img:
        img_b64, img_mime = None, None
    else:
        img_b64, img_mime = b["image_b64"], b["image_mime"]

    st.markdown("#### Vista previa")
    render_preview({"id": bid, "name": name or b["name"], "icon": icon, "color": color, "image_b64": img_b64,
                    "image_mime": img_mime, "start_s": start, "end_s": end, "volume": volume}, audio_b64)

    a1, a2, a3, _, a4 = st.columns([1.3, 1, 1, 0.4, 2.2])
    if a1.button("Guardar cambios", type="primary", icon=":material/save:", width="stretch"):
        fields = dict(name=name.strip() or b["name"], icon=icon, color=color, image_b64=img_b64,
                      image_mime=img_mime, start_s=float(start),
                      end_s=None if end >= dur - 0.01 else float(end), volume=float(volume))
        old_path = b["sound_path"]
        if new_snd is not None:
            fields["sound_path"] = save_uploaded_sound(user["username"], new_snd)
        update_button(user["id"], bid, **fields)
        if new_snd is not None:
            cleanup_sound(old_path)
        st.session_state.edit_ver = v + 1
        flash("Cambios guardados.")
        st.rerun()
    if a2.button("Subir", icon=":material/arrow_upward:", width="stretch"):
        move_button(user["id"], bid, -1)
        st.rerun()
    if a3.button("Bajar", icon=":material/arrow_downward:", width="stretch"):
        move_button(user["id"], bid, +1)
        st.rerun()
    with a4:
        confirm = st.checkbox("Confirmo que quiero eliminarlo", key=f"{k}_confirm")
        if st.button("Eliminar botón", icon=":material/delete:", disabled=not confirm, width="stretch"):
            delete_button(user["id"], bid)
            st.session_state.pop("edit_sel", None)
            st.session_state.edit_ver = v + 1
            flash(f"Botón «{b['name']}» eliminado.", ":material/delete:")
            st.rerun()

    st.divider()
    if st.button("Agregar otra vez los 10 botones de ejemplo", icon=":material/restore:"):
        seed_default_buttons(user["id"])
        flash("Botones de ejemplo agregados al final de tu set.")
        st.rerun()


def community_card(item: dict, me: dict) -> None:
    with st.container(border=True):
        show_html(build_pad_html([button_payload({**item, "id": f"c{item['id']}"})], "mini",
                                 st.session_state.stop_nonce), 112)
        author = item.get("display_name") or item["username"]
        st.markdown(
            f'<div class="ss-author">{avatar_html(item, 24)}<span>{esc(author)} '
            f'<span class="ss-muted">@{esc(item["username"])}</span></span></div>',
            unsafe_allow_html=True,
        )
        if item["description"]:
            st.caption(item["description"])
        st.caption(f"{item['saves']} guardados · {item['downloads']} descargas")

        c1, c2, c3 = st.columns([1.2, 1.2, 0.6])
        if item["saved_by_me"]:
            if c1.button("Guardar otra vez", key=f"save_{item['id']}", icon=":material/bookmark_added:",
                         width="stretch", help="Ya está en tu set; puedes agregar otra copia."):
                save_from_community(me["id"], me["username"], item)
                flash(f"«{item['name']}» agregado otra vez a tu set.")
                st.rerun()
        elif c1.button("Guardar", key=f"save_{item['id']}", icon=":material/bookmark_add:", type="primary",
                       width="stretch"):
            save_from_community(me["id"], me["username"], item)
            flash(f"«{item['name']}» guardado en tu SoundPad.")
            st.rerun()

        path = BASE_DIR / item["sound_path"]
        if path.exists():
            ext = path.suffix.lower()
            c2.download_button(
                "Descargar", data=path.read_bytes(), file_name=f"{slugify(item['name'])}{ext}",
                mime="audio/mpeg" if ext == ".mp3" else "audio/wav", key=f"dl_{item['id']}",
                icon=":material/download:", width="stretch", on_click=count_download, args=(item["id"],),
            )
        with c3.popover("", icon=":material/person:", help="Ver perfil del autor"):
            st.markdown(
                f'<div class="ss-profile">{avatar_html(item, 72)}<h3>{esc(author)}</h3>'
                f'<span class="ss-muted">@{esc(item["username"])}</span>'
                f'<div class="ss-bio">{esc(item.get("bio")) or "Sin bio todavía."}</div></div>',
                unsafe_allow_html=True,
            )
        if item["author_id"] == me["id"]:
            if st.button("Quitar de la comunidad", key=f"unpub_{item['id']}", icon=":material/remove_circle:",
                         type="tertiary"):
                unpublish(me["id"], item["id"])
                flash(f"«{item['name']}» ya no está en la comunidad.", ":material/remove_circle:")
                st.rerun()


def page_community(me: dict, buttons: list[dict]) -> None:
    page_header("Comunidad", PAGE_PAD, "Mi SoundPad", ":material/grid_view:")
    st.markdown('<p class="ss-muted">Escucha los botones que comparte la gente, guárdalos en tu set o descarga '
                'el audio.</p>', unsafe_allow_html=True)

    with st.expander("Publicar uno de mis botones", icon=":material/upload:"):
        if not buttons:
            st.info("Primero agrega un botón a tu set para poder publicarlo.")
        else:
            labels = {b["id"]: b["name"] for b in buttons}
            pid = st.selectbox("Botón", list(labels), format_func=labels.get, key="pub_sel")
            pb = next(b for b in buttons if b["id"] == pid)
            show_html(build_pad_html([button_payload({**pb, "id": f"p{pid}"})], "mini",
                                     st.session_state.stop_nonce), 112)
            pv = st.session_state.setdefault("pub_ver", 0)
            desc = st.text_area("Descripción (opcional)", max_chars=140, key=f"pub_desc_{pv}",
                                placeholder="Ej: ideal para cuando alguien se equivoca en la partida")
            st.caption("Al publicarlo, cualquier persona con cuenta podrá escucharlo, guardarlo y descargarlo.")
            if st.button("Publicar en la comunidad", type="primary", icon=":material/public:"):
                if not (BASE_DIR / pb["sound_path"]).exists():
                    st.error("No se encontró el archivo de sonido de este botón. Reemplázalo en Editar botones.")
                else:
                    publish_button(me["id"], pb, desc.strip())
                    st.session_state.pub_ver = pv + 1
                    flash(f"«{pb['name']}» publicado en la comunidad.", ":material/public:")
                    st.rerun()

    f1, f2, f3 = st.columns([2.2, 1, 1])
    query = f1.text_input("Buscar", placeholder="Nombre, descripción o autor", key="com_q",
                          icon=":material/search:", label_visibility="collapsed")
    order = f2.selectbox("Orden", ["Más recientes", "Más guardados", "Más descargados"], key="com_order",
                         label_visibility="collapsed")
    only_mine = f3.toggle("Solo mis publicaciones", key="com_mine")

    items = list_community(me["id"], query, order, only_mine)
    if not items:
        if query or only_mine:
            st.info("No hay botones que coincidan. Prueba con otra búsqueda o quita el filtro.")
        else:
            st.info("Todavía nadie ha publicado botones. Sé el primero desde «Publicar uno de mis botones».")
        return

    pages = max(1, math.ceil(len(items) / PER_PAGE))
    page = 1
    if pages > 1:
        page = st.segmented_control("Página", list(range(1, pages + 1)), default=1, key="com_page") or 1
    chunk = items[(page - 1) * PER_PAGE: page * PER_PAGE]
    cols = st.columns(3, gap="medium")
    for i, item in enumerate(chunk):
        with cols[i % 3]:
            community_card(item, me)


def page_profile(me: dict) -> None:
    st.markdown("## Mi perfil")
    stats = user_stats(me["id"])
    c1, c2 = st.columns([1, 1.6], gap="large")
    with c1:
        with st.container(border=True):
            st.markdown(
                f'<div class="ss-profile">{avatar_html(me, 110)}'
                f'<h3>{esc(me.get("display_name") or me["username"])}</h3>'
                f'<span class="ss-muted">@{esc(me["username"])}</span>'
                f'<span class="ss-muted">{esc(me.get("email")) or "Sin correo"}</span>'
                f'<div class="ss-bio">{esc(me.get("bio")) or "Todavía no escribes tu bio."}</div>'
                f'<div class="ss-stats"><div><b>{stats["buttons"]}</b><span>botones</span></div>'
                f'<div><b>{stats["published"]}</b><span>publicados</span></div>'
                f'<div><b>{stats["saves"]}</b><span>guardados por otros</span></div></div></div>',
                unsafe_allow_html=True,
            )
    with c2:
        pv = st.session_state.setdefault("profile_ver", 0)
        with st.form(f"profile_form_{pv}"):
            display = st.text_input("Nombre", me.get("display_name") or me["username"], max_chars=40)
            email = st.text_input("Correo", me.get("email") or "", placeholder="tucorreo@ejemplo.com")
            bio = st.text_area("Bio", me.get("bio") or "", max_chars=280,
                               placeholder="Cuéntale a la comunidad qué tipo de sonidos te gustan.")
            avatar = st.file_uploader("Foto de perfil", type=["png", "jpg", "jpeg", "webp"])
            remove = st.checkbox("Quitar la foto actual") if me.get("avatar_b64") else False
            ok = st.form_submit_button("Guardar perfil", type="primary", icon=":material/save:")
        if ok:
            email = email.strip()
            if email and not EMAIL_RE.match(email):
                st.error("Ese correo no parece válido. Revisa que tenga @ y un dominio.")
            elif email and email_taken(email, me["id"]):
                st.error("Ese correo ya está registrado en otra cuenta.")
            else:
                fields = {"display_name": display.strip() or me["username"], "email": email or None,
                          "bio": bio.strip()}
                if avatar is not None:
                    fields["avatar_b64"], fields["avatar_mime"] = image_to_b64(avatar, 256, square=True)
                elif remove:
                    fields["avatar_b64"], fields["avatar_mime"] = None, None
                update_profile(me["id"], **fields)
                st.session_state.profile_ver = pv + 1
                flash("Perfil actualizado.")
                st.rerun()

        st.caption(f"Tu usuario para iniciar sesión es **@{me['username']}** (también puedes entrar con tu correo).")
        with st.expander("Cambiar contraseña", icon=":material/lock:"):
            with st.form("pw_form", clear_on_submit=True):
                cur = st.text_input("Contraseña actual", type="password")
                new = st.text_input("Nueva contraseña", type="password")
                new2 = st.text_input("Repite la nueva contraseña", type="password")
                okp = st.form_submit_button("Cambiar contraseña")
            if okp:
                if len(new) < 4:
                    st.error("La nueva contraseña debe tener al menos 4 caracteres.")
                elif new != new2:
                    st.error("Las contraseñas nuevas no coinciden.")
                elif not change_password(me["id"], cur, new):
                    st.error("La contraseña actual no es correcta.")
                else:
                    st.success("Contraseña cambiada.")


# ================================================================== MAIN
def main() -> None:
    ensure_default_sounds()
    init_db()
    st.markdown(APP_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("stop_nonce", 0)
    if "edit_mode" in st.session_state:  # mantiene el modo edición al ir y volver de otra sección
        st.session_state.edit_mode = st.session_state.edit_mode

    session_user = st.session_state.get("user")
    me = get_user(session_user["id"]) if session_user else None
    if not me:
        st.session_state.pop("user", None)
        show_flash()
        page_auth()
        return

    buttons = get_buttons(me["id"])
    page = sidebar(me, len(buttons))
    show_flash()

    if page == PAGE_COMMUNITY:
        page_community(me, buttons)
    elif page == PAGE_ADD:
        page_add(me)
    elif page == PAGE_EDIT:
        page_edit(me, buttons)
    elif page == PAGE_PROFILE:
        page_profile(me)
    else:
        page_pad(me, buttons)


main()
