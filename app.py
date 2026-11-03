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
        requests/    -> sonidos entregados por artistas en Imaginación
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
import random
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import unicodedata
import uuid
import wave
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import streamlit as st
import streamlit.components.v1 as components

try:
    from mutagen import File as MutagenFile
except Exception:  # mutagen es opcional (solo sirve para leer la duración de los MP3)
    MutagenFile = None

try:
    import miniaudio  # decodifica MP3/WAV sin ffmpeg para ajustar la calidad según el plan
except Exception:
    miniaudio = None

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
REQUESTS_DIR = SOUNDS_DIR / "requests"
DB_PATH = BASE_DIR / "soundset.db"
SR = 22050  # frecuencia de muestreo de los sonidos de ejemplo
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PER_PAGE = 12

# Pagos: "demo" registra los pagos sin cobrar dinero real.
# Para cobrar de verdad, conecta Mercado Pago, Flow o Stripe dentro de process_payment().
PAYMENT_MODE = "demo"

PLANS = {
    "free": {"name": "Estándar", "price": 0, "limit": 20, "sample_rate": 22050, "channels": 1,
             "kbps": 352.8, "quality": "Calidad intermedia (hasta 352 kbps · 22 kHz mono)",
             "obs": False, "sell": False, "artist": False},
    "premium": {"name": "Premium", "price": 4, "limit": 100, "sample_rate": 44100, "channels": 2,
                "kbps": 1411.2, "quality": "Calidad CD (hasta 1411 kbps · 44,1 kHz estéreo)",
                "obs": True, "sell": True, "artist": False},
    "soundset": {"name": "SoundSet", "price": 10, "limit": None, "sample_rate": 44100, "channels": 2,
                 "kbps": 1411.2, "quality": "Calidad CD (hasta 1411 kbps · 44,1 kHz estéreo)",
                 "obs": True, "sell": True, "artist": True},
}
CURRENCIES = ["CLP", "USD"]

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

PAGE_HOME = ":material/bubble_chart: Inicio"
PAGE_PAD = ":material/grid_view: Mi SoundPad"
PAGE_COMMUNITY = ":material/groups: Comunidad"
PAGE_ADD = ":material/add_circle: Agregar botón"
PAGE_EDIT = ":material/tune: Editar botones"
PAGE_PROFILE = ":material/person: Mi perfil"
PAGE_OBS = ":material/cast: Stream / OBS"
PAGE_PLANS = ":material/workspace_premium: Planes"
# Inicio (Imaginación) primero; Comunidad justo debajo de Mi SoundPad para llegar más rápido
PAGES = [PAGE_HOME, PAGE_PAD, PAGE_COMMUNITY, PAGE_ADD, PAGE_EDIT, PAGE_OBS, PAGE_PLANS, PAGE_PROFILE]

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
    for folder in (DEFAULT_DIR, USER_SOUNDS_DIR, COMMUNITY_DIR, REQUESTS_DIR):
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
            CREATE TABLE IF NOT EXISTS purchases(
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES community(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, item_id)
            );
            CREATE TABLE IF NOT EXISTS payments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                payee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                kind TEXT NOT NULL,
                ref_id INTEGER,
                description TEXT,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                budget REAL,
                currency TEXT,
                color TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                artist_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                agreed_amount REAL,
                agreed_currency TEXT,
                sound_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS offers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
                artist_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                message TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
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
                          ("avatar_b64", "TEXT"), ("avatar_mime", "TEXT"),
                          ("plan", "TEXT DEFAULT 'free'"), ("plan_expires", "TEXT"),
                          ("obs_token", "TEXT"), ("twitch_channel", "TEXT"), ("twitch_client_id", "TEXT"),
                          ("twitch_token", "TEXT"), ("chat_enabled", "INTEGER DEFAULT 0"),
                          ("chat_prefix", "TEXT DEFAULT '!sonido'"), ("obs_cooldown", "INTEGER DEFAULT 5")]:
            _ensure_column(con, "users", col, decl)
        _ensure_column(con, "community", "price_usd", "REAL NOT NULL DEFAULT 0")
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
                "INSERT INTO users(username, pw_hash, salt, display_name, email, bio, plan) VALUES (?, ?, ?, ?, ?, '', 'free')",
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


# ------------------------------------------------------------------ planes y pagos
def active_plan(u: dict) -> str:
    """Plan vigente. Si un plan pagado venció, la cuenta vuelve a Estándar (sin borrar sus sonidos)."""
    plan = u.get("plan") or "free"
    if plan != "free" and (not u.get("plan_expires") or u["plan_expires"] < date.today().isoformat()):
        return "free"
    return plan if plan in PLANS else "free"


def plan_of(u: dict) -> dict:
    return PLANS[active_plan(u)]


def room_left(u: dict, n_buttons: int) -> float:
    limit = plan_of(u)["limit"]
    return math.inf if limit is None else max(0, limit - n_buttons)


def fmt_money(amount: float, currency: str) -> str:
    if currency == "CLP":
        return f"${int(round(amount)):,} CLP".replace(",", ".")
    return f"US${amount:,.2f}"


def record_payment(con, user_id, payee_id, kind, ref_id, description, amount, currency, status) -> None:
    con.execute(
        "INSERT INTO payments(user_id, payee_id, kind, ref_id, description, amount, currency, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, payee_id, kind, ref_id, description, amount, currency, status),
    )


def process_payment(amount: float, currency: str, description: str) -> bool:
    """Aquí va la pasarela real (Mercado Pago, Flow, Stripe…). En modo demo siempre se aprueba."""
    if PAYMENT_MODE == "demo":
        return True
    raise NotImplementedError("Conecta tu pasarela de pago en process_payment().")


def buy_plan(uid: int, plan: str) -> None:
    u = get_user(uid)
    today = date.today()
    base = today
    if active_plan(u) == plan and u.get("plan_expires"):
        base = max(today, date.fromisoformat(u["plan_expires"]))  # renovar suma un año más
    expires = (base + timedelta(days=365)).isoformat()
    with db() as con:
        con.execute("UPDATE users SET plan = ?, plan_expires = ? WHERE id = ?", (plan, expires, uid))
        record_payment(con, uid, None, "plan", None, f"Plan {PLANS[plan]['name']} (1 año)",
                       PLANS[plan]["price"], "USD", "paid")


def cancel_plan(uid: int) -> None:
    with db() as con:
        con.execute("UPDATE users SET plan = 'free', plan_expires = NULL WHERE id = ?", (uid,))


def my_payments(uid: int) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM payments WHERE user_id = ? OR payee_id = ? ORDER BY id DESC",
                           (uid, uid)).fetchall()
    return [dict(r) for r in rows]


def earnings(uid: int) -> dict:
    out = {"available": {}, "held": {}}
    with db() as con:
        for r in con.execute("SELECT amount, currency, status FROM payments WHERE payee_id = ?", (uid,)):
            bucket = "held" if r["status"] == "held" else "available"
            out[bucket][r["currency"]] = out[bucket].get(r["currency"], 0) + r["amount"]
    return out


# ------------------------------------------------------------------ calidad de audio
def audio_kbps(data: bytes, ext: str) -> float | None:
    if ext == ".wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                return w.getframerate() * w.getnchannels() * w.getsampwidth() * 8 / 1000
        except Exception:
            return None
    if MutagenFile is not None:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(data)
            p = Path(tmp.name)
        try:
            m = MutagenFile(str(p))
            if m is not None and getattr(m.info, "bitrate", None):
                return m.info.bitrate / 1000
        except Exception:
            return None
        finally:
            p.unlink(missing_ok=True)
    return None


def conform_quality(data: bytes, ext: str, plan: dict) -> tuple[bytes, str, str | None]:
    """Si el audio supera la calidad máxima del plan, lo convierte a WAV al máximo permitido."""
    kbps = audio_kbps(data, ext)
    if kbps is not None and kbps <= plan["kbps"] + 0.5:
        return data, ext, None
    if miniaudio is None:
        return data, ext, None
    try:
        dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=plan["channels"], sample_rate=plan["sample_rate"])
    except Exception:
        return data, ext, None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(dec.nchannels)
        w.setsampwidth(2)
        w.setframerate(dec.sample_rate)
        w.writeframes(dec.samples.tobytes())
    before = f"{kbps:.0f} kbps" if kbps else "más de lo permitido"
    return buf.getvalue(), ".wav", f"El audio venía a {before}; se ajustó a {int(plan['kbps'])} kbps según tu plan."


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


def publish_button(uid: int, button: dict, description: str, price_usd: float = 0.0) -> None:
    path = _copy_sound(button["sound_path"], COMMUNITY_DIR, "sounds/community")
    with db() as con:
        con.execute(
            "INSERT INTO community(author_id, name, description, icon, image_b64, image_mime, color, sound_path,"
            " start_s, end_s, volume, price_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, button["name"], description, button["icon"], button["image_b64"], button["image_mime"],
             button["color"], path, button["start_s"], button["end_s"], button["volume"], price_usd),
        )


def buy_download(uid: int, item: dict) -> None:
    with db() as con:
        con.execute("INSERT OR IGNORE INTO purchases(user_id, item_id) VALUES (?, ?)", (uid, item["id"]))
        record_payment(con, uid, item["author_id"], "sound", item["id"], f"Descarga «{item['name']}»",
                       item["price_usd"], "USD", "paid")


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
        " EXISTS(SELECT 1 FROM community_saves s WHERE s.item_id = c.id AND s.user_id = ?) AS saved_by_me, "
        " EXISTS(SELECT 1 FROM purchases p WHERE p.item_id = c.id AND p.user_id = ?) AS bought_by_me "
        "FROM community c JOIN users u ON u.id = c.author_id "
        "WHERE (c.name LIKE ? OR c.description LIKE ? OR u.username LIKE ? OR u.display_name LIKE ?)"
    )
    params: list = [uid, uid, like, like, like, like]
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


# ------------------------------------------------------------------ Imaginación (pedidos a artistas)
def create_request(uid: int, text: str, kind: str, budget: float | None, currency: str | None, color: str) -> None:
    with db() as con:
        con.execute("INSERT INTO requests(user_id, text, kind, budget, currency, color) VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, text, kind, budget, currency, color))


_REQ_SELECT = ("SELECT r.*, u.username, u.display_name, u.avatar_b64, u.avatar_mime "
               "FROM requests r JOIN users u ON u.id = r.user_id ")


def open_requests(exclude_uid: int) -> list[dict]:
    """Burbujas abiertas de otras personas, marcando si el artista ya envió una oferta."""
    with db() as con:
        rows = con.execute(
            _REQ_SELECT.replace("FROM", ", EXISTS(SELECT 1 FROM offers o WHERE o.request_id = r.id "
                                        "AND o.artist_id = ? AND o.status = 'pending') AS offered FROM")
            + "WHERE r.status = 'open' AND r.user_id != ? ORDER BY r.id DESC",
            (exclude_uid, exclude_uid)).fetchall()
    return [dict(r) for r in rows]


def count_open_requests() -> int:
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM requests WHERE status = 'open'").fetchone()[0]


def my_requests(uid: int) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT r.*, u.username, u.display_name, a.username AS artist_username FROM requests r "
            "JOIN users u ON u.id = r.user_id LEFT JOIN users a ON a.id = r.artist_id "
            "WHERE r.user_id = ? AND r.status != 'closed' ORDER BY r.id DESC", (uid,)).fetchall()
    return [dict(r) for r in rows]


def save_request_sound(uid: int, username: str, req: dict) -> None:
    path = _copy_sound(req["sound_path"], USER_SOUNDS_DIR / username.lower(), f"sounds/users/{username.lower()}")
    name = req["text"].strip()
    name = name if len(name) <= 24 else name[:23].rstrip() + "…"
    add_button(uid, name=name, icon="wave", color=req["color"], sound_path=path)


def request_offers(req_id: int) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT o.*, u.username, u.display_name, u.avatar_b64, u.avatar_mime, u.bio FROM offers o "
            "JOIN users u ON u.id = o.artist_id WHERE o.request_id = ? AND o.status = 'pending' ORDER BY o.id",
            (req_id,)).fetchall()
    return [dict(r) for r in rows]


def pending_offers_count(uid: int) -> int:
    with db() as con:
        return con.execute(
            "SELECT COUNT(*) FROM offers o JOIN requests r ON r.id = o.request_id "
            "WHERE r.user_id = ? AND r.status = 'open' AND o.status = 'pending'", (uid,)).fetchone()[0]


def create_offer(artist_id: int, req_id: int, amount: float, currency: str, message: str) -> None:
    with db() as con:
        con.execute("INSERT INTO offers(request_id, artist_id, amount, currency, message) VALUES (?, ?, ?, ?, ?)",
                    (req_id, artist_id, amount, currency, message))


def reject_offer(uid: int, offer_id: int) -> None:
    with db() as con:
        con.execute("UPDATE offers SET status = 'rejected' WHERE id = ? AND request_id IN "
                    "(SELECT id FROM requests WHERE user_id = ?)", (offer_id, uid))


def withdraw_offer(artist_id: int, offer_id: int) -> None:
    with db() as con:
        con.execute("UPDATE offers SET status = 'withdrawn' WHERE id = ? AND artist_id = ? AND status = 'pending'",
                    (offer_id, artist_id))


def accept_offer(uid: int, offer: dict, req: dict) -> None:
    """La persona acepta y paga: el dinero queda retenido hasta que el artista entregue el sonido."""
    with db() as con:
        con.execute("UPDATE offers SET status = 'accepted' WHERE id = ?", (offer["id"],))
        con.execute("UPDATE offers SET status = 'rejected' WHERE request_id = ? AND id != ? AND status = 'pending'",
                    (req["id"], offer["id"]))
        con.execute("UPDATE requests SET status = 'in_progress', artist_id = ?, agreed_amount = ?, "
                    "agreed_currency = ? WHERE id = ? AND user_id = ?",
                    (offer["artist_id"], offer["amount"], offer["currency"], req["id"], uid))
        record_payment(con, uid, offer["artist_id"], "request", req["id"], f"Pedido: {req['text'][:60]}",
                       offer["amount"], offer["currency"], "held")


def close_request(uid: int, req_id: int) -> None:
    with db() as con:
        con.execute("UPDATE requests SET status = 'closed' WHERE id = ? AND user_id = ? AND status = 'open'",
                    (req_id, uid))
        con.execute("UPDATE offers SET status = 'rejected' WHERE request_id = ? AND status = 'pending'", (req_id,))


def artist_jobs(artist_id: int) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT o.id AS offer_id, o.amount, o.currency, o.message, o.status AS offer_status, r.*, "
            "u.username, u.display_name FROM offers o JOIN requests r ON r.id = o.request_id "
            "JOIN users u ON u.id = r.user_id WHERE o.artist_id = ? AND o.status IN ('pending', 'accepted', 'rejected') "
            "ORDER BY o.id DESC", (artist_id,)).fetchall()
    return [dict(r) for r in rows]


def deliver_request(artist_id: int, req_id: int, uploaded) -> None:
    ext = Path(uploaded.name).suffix.lower()
    name = f"{uuid.uuid4().hex[:12]}{ext}"
    (REQUESTS_DIR / name).write_bytes(uploaded.getvalue())
    with db() as con:
        con.execute("UPDATE requests SET status = 'delivered', sound_path = ? WHERE id = ? AND artist_id = ?",
                    (f"sounds/requests/{name}", req_id, artist_id))
        con.execute("UPDATE payments SET status = 'released' WHERE kind = 'request' AND ref_id = ? AND status = 'held'",
                    (req_id,))


# ------------------------------------------------------------------ OBS
def ensure_obs_token(uid: int, regenerate: bool = False) -> str:
    u = get_user(uid)
    if u.get("obs_token") and not regenerate:
        return u["obs_token"]
    token = secrets.token_urlsafe(18)
    with db() as con:
        con.execute("UPDATE users SET obs_token = ? WHERE id = ?", (token, uid))
    return token


def user_by_obs_token(token: str) -> dict | None:
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE obs_token = ?", (token,)).fetchone()
    return dict(row) if row else None


def update_stream_settings(uid: int, **fields) -> None:
    allowed = {"twitch_channel", "twitch_client_id", "twitch_token", "chat_enabled", "chat_prefix", "obs_cooldown"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        with db() as con:
            con.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                        (*fields.values(), uid))


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


def save_uploaded_sound(username: str, uploaded, plan: dict | None = None) -> str:
    ext = Path(uploaded.name).suffix.lower()
    data = uploaded.getvalue()
    if plan is not None:
        data, ext, note = conform_quality(data, ext, plan)
        if note:
            flash(note, ":material/high_quality:")
    folder = USER_SOUNDS_DIR / username.lower()
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex[:12]}{ext}"
    (folder / fname).write_bytes(data)
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


OVERLAY_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;background:transparent;font-family:'Segoe UI',system-ui,sans-serif;color:#fff;overflow:hidden}
body{min-height:240px}
#feed{position:fixed;left:16px;bottom:16px;display:flex;flex-direction:column;gap:8px}
.note{--c:#ff6a00;display:flex;align-items:center;gap:12px;padding:8px 18px 8px 8px;border-radius:999px;
 background:rgba(20,16,14,.9);border:2px solid var(--c);box-shadow:0 6px 18px rgba(0,0,0,.4);
 animation:in .25s ease-out, out .4s ease-in 3.6s forwards}
.ic{width:38px;height:38px;border-radius:50%;background:var(--c);display:flex;align-items:center;justify-content:center;overflow:hidden;flex:none}
.ic svg{width:22px;height:22px;color:#fff}.ic img{width:100%;height:100%;object-fit:cover}
.t b{display:block;font-size:16px}.t span{font-size:13px;opacity:.75}
@keyframes in{from{transform:translateY(12px);opacity:0}}@keyframes out{to{opacity:0;transform:translateY(-6px)}}
#test{display:none;padding:12px 14px;background:rgba(20,16,14,.92);border-radius:14px;margin:8px;max-width:760px}
#test.on{display:block}#status{font-size:13px;opacity:.85;margin-bottom:8px;line-height:1.5}
#chips button{--c:#ff6a00;margin:3px;padding:6px 12px;border-radius:999px;border:none;background:var(--c);color:#fff;font-weight:600;cursor:pointer}
</style></head>
<body><div id="test"><div id="status">Conectando…</div><div id="chips"></div></div><div id="feed"></div>
<script>
const DATA = __DATA__, CFG = __CFG__;
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
const byName = {}; DATA.forEach(b => byName[norm(b.name)] = b);
const AC = window.AudioContext || window.webkitAudioContext, ctx = new AC();
const buffers = {}, last = {};
function b64ToBuf(b64){const bin=atob(b64);const u=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return u.buffer;}
DATA.forEach(b => { buffers[b.id] = b.audio ? new Promise(r => ctx.decodeAudioData(b64ToBuf(b.audio), r, () => r(null))) : Promise.resolve(null); });
const statusLines = {};
function status(key, text){ statusLines[key] = text; document.getElementById('status').innerHTML =
  Object.values(statusLines).map(t => t.replace(/</g,'&lt;')).join('<br>'); console.log('[SoundSet]', text); }

async function play(b){
  if (ctx.state !== 'running') { try { await ctx.resume(); } catch(e){} }
  const buf = await buffers[b.id]; if (!buf) return;
  const s = Math.min(Math.max(b.start || 0, 0), buf.duration);
  const e = (b.end && b.end > s) ? Math.min(b.end, buf.duration) : buf.duration;
  const src = ctx.createBufferSource(), g = ctx.createGain();
  src.buffer = buf; g.gain.value = b.volume ?? 1; src.connect(g); g.connect(ctx.destination);
  src.start(0, s, Math.max(0.02, e - s));
}
function notify(b, who, via){
  const n = document.createElement('div'); n.className = 'note'; n.style.setProperty('--c', b.color);
  const ic = document.createElement('div'); ic.className = 'ic';
  if (b.img) { const im = document.createElement('img'); im.src = b.img; ic.appendChild(im); } else ic.innerHTML = b.svg;
  const t = document.createElement('div'); t.className = 't';
  const nb = document.createElement('b'); nb.textContent = b.name;
  const ns = document.createElement('span'); ns.textContent = who ? `${who} · ${via}` : via;
  t.append(nb, ns); n.append(ic, t);
  document.getElementById('feed').appendChild(n); setTimeout(() => n.remove(), 4100);
}
function trigger(name, who, via){
  const b = byName[norm(name)];
  if (!b) { status('last', `Sin botón llamado «${name}»`); return; }
  const now = Date.now();
  if (CFG.cooldown && last[b.id] && now - last[b.id] < CFG.cooldown * 1000) return;  // enfriamiento por sonido
  last[b.id] = now; play(b); notify(b, who, via);
}

// ---- Puntos del canal (Twitch EventSub por WebSocket)
async function subscribe(sessionId){
  const h = {'Client-Id': CFG.clientId, 'Authorization': 'Bearer ' + CFG.token, 'Content-Type': 'application/json'};
  try {
    const me = await (await fetch('https://api.twitch.tv/helix/users', {headers: h})).json();
    if (!me.data || !me.data.length) { status('pts', 'Puntos del canal: el token no es válido o venció.'); return; }
    const r = await fetch('https://api.twitch.tv/helix/eventsub/subscriptions', {method: 'POST', headers: h, body: JSON.stringify({
      type: 'channel.channel_points_custom_reward_redemption.add', version: '1',
      condition: {broadcaster_user_id: me.data[0].id}, transport: {method: 'websocket', session_id: sessionId}})});
    status('pts', r.ok ? `Puntos del canal conectados (${me.data[0].display_name})` : `Puntos del canal: error ${r.status}. Revisa el permiso channel:read:redemptions.`);
  } catch(e) { status('pts', 'Puntos del canal: no se pudo conectar con Twitch.'); }
}
function eventsub(url){
  const ws = new WebSocket(url || 'wss://eventsub.wss.twitch.tv/ws'); let moved = false;
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data), type = m.metadata && m.metadata.message_type;
    if (type === 'session_welcome' && !url) subscribe(m.payload.session.id);
    else if (type === 'notification' && m.payload.subscription.type === 'channel.channel_points_custom_reward_redemption.add') {
      const e = m.payload.event; trigger(e.reward.title, e.user_name, 'puntos del canal');
    } else if (type === 'session_reconnect') { moved = true; eventsub(m.payload.session.reconnect_url); }
  };
  ws.onclose = () => { if (!moved) { status('pts', 'Puntos del canal: reconectando…'); setTimeout(() => eventsub(), 5000); } };
}

// ---- Comandos de chat (lectura anónima del chat de Twitch)
function chat(){
  const ws = new WebSocket('wss://irc-ws.chat.twitch.tv:443'), prefix = norm(CFG.prefix || '!sonido');
  ws.onopen = () => { ws.send('CAP REQ :twitch.tv/tags'); ws.send('PASS SCHMOOPIIE');
    ws.send('NICK justinfan' + Math.floor(Math.random() * 80000 + 1000)); ws.send('JOIN #' + CFG.channel.toLowerCase());
    status('chat', `Chat conectado: escribe «${CFG.prefix} nombre» en #${CFG.channel}`); };
  ws.onmessage = ev => ev.data.split('\r\n').forEach(line => {
    if (line.startsWith('PING')) { ws.send('PONG :tmi.twitch.tv'); return; }
    const m = line.match(/display-name=([^;]*);.* PRIVMSG #\S+ :(.*)$/);
    if (m && norm(m[2]).startsWith(prefix + ' ')) trigger(m[2].slice(prefix.length + 1), m[1], 'chat');
  });
  ws.onclose = () => { status('chat', 'Chat: reconectando…'); setTimeout(chat, 5000); };
}

if (CFG.clientId && CFG.token) eventsub(); else status('pts', 'Puntos del canal: falta el Client ID o el token en SoundSet.');
if (CFG.chat && CFG.channel) chat();
status('n', `${DATA.length} sonidos cargados`);
if (CFG.test) {
  document.getElementById('test').classList.add('on');
  const chips = document.getElementById('chips');
  DATA.forEach(b => { const c = document.createElement('button'); c.textContent = b.name; c.style.setProperty('--c', b.color);
    c.onclick = () => trigger(b.name, 'prueba', 'clic'); chips.appendChild(c); });
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


def build_overlay_html(payload: list[dict], cfg: dict) -> str:
    dump = lambda x: json.dumps(x, ensure_ascii=False).replace("</", "<\\/")
    return OVERLAY_TEMPLATE.replace("__DATA__", dump(payload)).replace("__CFG__", dump(cfg))


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
/* burbujas de Imaginación */
.ss-bubble { --b: #e53935; position: relative; padding: 1.4rem 1.6rem 1.1rem; margin: .4rem 0 .6rem;
  background: color-mix(in srgb, var(--b) 15%, #14100e);
  border: 1.5px solid color-mix(in srgb, var(--b) 42%, transparent);
  color: color-mix(in srgb, var(--b) 68%, #cfc5bd);
  border-radius: 46% 54% 52% 48% / 38% 42% 58% 62%;
  box-shadow: inset 0 0 38px color-mix(in srgb, var(--b) 16%, transparent);
  animation: ss-float 7s ease-in-out infinite; }
.ss-bubble:nth-child(even) { animation-delay: -3s; }
.ss-bubble p { font-weight: 600; font-size: 1.02rem; line-height: 1.4; margin: 0 0 .6rem; color: inherit; text-align: center; }
.ss-bubble.big { max-width: 560px; min-height: 270px; margin: .8rem auto .4rem; padding: 2.6rem 3.2rem 2rem;
  border-radius: 50%; display: flex; flex-direction: column; justify-content: center; }
.ss-bubble.big p { font-size: 1.45rem; }
.ss-bubble .meta { display: flex; justify-content: center; gap: .9rem; flex-wrap: wrap; font-size: .82rem;
  color: color-mix(in srgb, var(--b) 45%, #9a8f86); }
.ss-bubble .price { font-weight: 700; color: color-mix(in srgb, var(--b) 75%, #e8ddd5); }
@keyframes ss-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
@media (prefers-reduced-motion: reduce) { .ss-bubble { animation: none; } }
.ss-hero { padding: .4rem 0 1rem; }
.ss-hero h1 { display: inline-block; font-size: 3rem; font-weight: 800; letter-spacing: -1.5px; margin: 0; padding: 0;
  background: linear-gradient(90deg, #e53935, #ff6d00, #f9b800, #2e9e4f, #1e6fe0, #8e24aa, #e91e7a);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.ss-dots { display: flex; gap: 6px; margin: .2rem 0 .6rem; }
.ss-dots i { width: 14px; height: 14px; border-radius: 50%; display: block; opacity: .85; }
.ss-badge { display: inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .78rem; font-weight: 600;
  background: #2a211c; color: #d9cfc6; border: 1px solid #3a2e27; }
.ss-badge.orange { background: #ff6a00; color: #1a1613; border-color: #ff6a00; }
/* planes */
/* cada columna estira su tarjeta para que las tres midan lo mismo; en celular se apilan con su botón */
[data-testid="stHorizontalBlock"]:has(.ss-plan) { position: relative; z-index: 0; align-items: stretch;
  flex-wrap: wrap; container-type: inline-size; }
@container (max-width: 760px) {
  [data-testid="stColumn"]:has(.ss-plan) { flex: 1 1 100% !important; min-width: 100% !important; margin-bottom: .6rem; }
}
[data-testid="stColumn"]:has(.ss-plan) > [data-testid="stVerticalBlock"] { height: 100%; }
[data-testid="stElementContainer"]:has(.ss-plan) { flex: 1 1 auto; }
[data-testid="stElementContainer"]:has(.ss-plan) div:has(.ss-plan) { height: 100%; align-items: stretch; }
.ss-plan { position: relative; box-sizing: border-box; height: 100%; display: flex; flex-direction: column; border-radius: 22px;
  padding: 1.5rem 1.5rem 1.4rem; background: #151110; border: 1px solid #2a2320; }
.ss-plan h3 { margin: 0; padding: 0; font-size: 1.45rem; font-weight: 700; color: #f4efe9; }
.ss-plan.has-cur h3 { padding-right: 4.4rem; }
.ss-plan, .ss-plan * { word-break: normal; overflow-wrap: normal; }
.ss-plan .tg { color: #8d847d; font-size: .9rem; margin-top: .1rem; }
.ss-plan .pr { font-size: 2.5rem; font-weight: 800; line-height: 1.1; margin: .9rem 0 0; color: #f4efe9; }
.ss-plan .pr small { font-size: .95rem; font-weight: 500; color: #8d847d; -webkit-text-fill-color: #8d847d; }
.ss-plan ul { list-style: none; padding: 1.1rem 0 0; margin: 1.1rem 0 0; border-top: 1px solid #2a2320;
  display: flex; flex-direction: column; gap: .7rem; }
.ss-plan li { position: relative; padding-left: 1.75rem; margin: 0; color: #d9cfc6; line-height: 1.4; font-size: .98rem; }
.ss-plan li::before { content: "✓"; position: absolute; left: 0; top: .1rem; width: 1.15rem; height: 1.15rem;
  border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .7rem;
  font-weight: 800; background: var(--dot, #3a322d); color: var(--dot-ink, #f4efe9); }
.ss-plan .cur { position: absolute; top: 1.25rem; right: 1.2rem; font-size: .75rem; font-weight: 600;
  padding: .15rem .65rem; border-radius: 999px; background: #2a2320; color: #f4efe9; }
/* Premium: dorado */
.ss-plan.gold { border: 1.5px solid transparent;
  background: linear-gradient(160deg, #1d170d, #141009) padding-box,
              linear-gradient(135deg, #f9e29c, #c9962b 40%, #f5d27a 65%, #9c6e17) border-box;
  box-shadow: 0 0 34px rgba(212, 175, 55, .12); --dot: #e3bd59; --dot-ink: #1d170d; }
.ss-plan.gold h3 { color: #f5d27a; }
.ss-plan.gold .pr { background: linear-gradient(90deg, #f9e29c, #d4af37, #f5d27a);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.ss-plan.gold ul { border-top-color: #3a2f18; }
.ss-plan.gold .cur { background: #e3bd59; color: #1d170d; }
/* SoundSet: borde RGBIC */
.ss-plan.rgb { border: 2px solid transparent;
  background: linear-gradient(#151110, #151110) padding-box,
              linear-gradient(90deg, #d4af37, #e53935, #ff6d00, #8e24aa, #1e6fe0, #d4af37) border-box;
  background-size: 100% 100%, 300% 100%; animation: ss-rgb 6s linear infinite; --dot: #ff6d00; --dot-ink: #151110; }
.ss-plan.rgb::before { content: ""; position: absolute; inset: -2px; border-radius: inherit; z-index: -1;
  background: linear-gradient(90deg, #d4af37, #e53935, #ff6d00, #8e24aa, #1e6fe0, #d4af37);
  background-size: 300% 100%; filter: blur(20px); opacity: .32; animation: ss-rgb-glow 6s linear infinite; }
.ss-plan.rgb .pr { background: linear-gradient(90deg, #d4af37, #e53935, #ff6d00, #8e24aa, #1e6fe0, #d4af37);
  background-size: 300% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: ss-rgb-glow 6s linear infinite; }
.ss-plan.rgb .cur { background: linear-gradient(90deg, #e53935, #8e24aa); color: #fff; }
@keyframes ss-rgb { to { background-position: 0 0, 300% 0; } }
@keyframes ss-rgb-glow { to { background-position: 300% 0; } }
.st-key-plan_premium button { background: linear-gradient(135deg, #f5d27a, #c9962b) !important; border: none !important;
  color: #1d170d !important; font-weight: 700 !important; }
.st-key-plan_soundset button { border: none !important; color: #fff !important; font-weight: 700 !important;
  background: linear-gradient(90deg, #d4af37, #e53935, #ff6d00, #8e24aa, #1e6fe0, #d4af37) !important;
  background-size: 300% 100% !important; animation: ss-rgb-glow 6s linear infinite;
  text-shadow: 0 1px 2px rgba(0,0,0,.45); }
@media (prefers-reduced-motion: reduce) { .ss-plan.rgb, .ss-plan.rgb::before, .ss-plan.rgb .pr,
  .st-key-plan_soundset button { animation: none !important; } }
.st-key-hide_ui { display: none; }
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
    st.session_state.setdefault("flash", []).append((msg, icon))


def show_flash() -> None:
    for msg, icon in st.session_state.pop("flash", []):
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
            f'<div class="ss-muted">@{esc(me["username"])}</div></div></div>',
            unsafe_allow_html=True,
        )
        plan = plan_of(me)
        limit = "∞" if plan["limit"] is None else plan["limit"]
        st.markdown(f'<div style="margin-top:.6rem"><span class="ss-badge{" orange" if plan["price"] else ""}">'
                    f'Plan {plan["name"]}</span> <span class="ss-muted">{n_buttons}/{limit} sonidos</span></div>',
                    unsafe_allow_html=True)
        offers = pending_offers_count(me["id"])
        if offers:
            st.caption(f"Tienes {offers} oferta(s) nueva(s) en Imaginación.")
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


def can_add(me: dict, n_buttons: int, key: str, needed: int = 1) -> bool:
    """Muestra un aviso con acceso a Planes si el plan no permite agregar más sonidos."""
    if room_left(me, n_buttons) >= needed:
        return True
    plan = plan_of(me)
    st.warning(f"Tu plan {plan['name']} permite hasta {plan['limit']} sonidos y ya tienes {n_buttons}. "
               "Mejora tu plan para agregar más.", icon=":material/lock:")
    st.button("Ver planes", key=f"upgrade_{key}", icon=":material/workspace_premium:", on_click=go_to,
              args=(PAGE_PLANS,))
    return False


@st.dialog("Pagar")
def checkout(title: str, amount: float, currency: str, action, *args) -> None:
    st.markdown(f"**{esc(title)}**")
    st.markdown(f"Total: **{fmt_money(amount, currency)}**")
    if PAYMENT_MODE == "demo":
        st.info("Modo demo: no se cobra dinero real. Cuando conectes una pasarela de pago, aquí se abrirá "
                "el pago seguro.", icon=":material/info:")
    c1, c2 = st.columns(2)
    if c1.button("Cancelar", key="co_cancel", width="stretch"):
        st.rerun()
    if c2.button("Pagar", key="co_pay", type="primary", icon=":material/lock:", width="stretch"):
        if process_payment(amount, currency, title):
            action(*args)
            flash("Pago aprobado.", ":material/verified:")
            st.rerun()


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
                    full = room_left(me, len(buttons)) <= 0
                    st.button("Mejorar plan" if full else "Agregar", key="tile_add",
                              icon=":material/workspace_premium:" if full else ":material/add:",
                              on_click=go_to, args=(PAGE_PLANS if full else PAGE_ADD,), width="stretch")
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


def page_add(user: dict, n_buttons: int) -> None:
    v = st.session_state.setdefault("add_ver", 0)
    page_header("Agregar botón", PAGE_PAD, "Mi SoundPad", ":material/grid_view:")
    plan = plan_of(user)
    if not can_add(user, n_buttons, "add"):
        return
    st.caption(f"Plan {plan['name']}: {plan['quality']}.")
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
                sound_path=save_uploaded_sound(user["username"], snd, plan), start_s=float(start),
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
        if can_add(user, 0, "seed0", needed=len(DEFAULT_SOUNDS)) and st.button(
                "Recuperar los 10 botones de ejemplo", type="primary", icon=":material/restore:"):
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
            fields["sound_path"] = save_uploaded_sound(user["username"], new_snd, plan_of(user))
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
    if room_left(user, len(buttons)) >= len(DEFAULT_SOUNDS) and st.button(
            "Agregar otra vez los 10 botones de ejemplo", icon=":material/restore:"):
        seed_default_buttons(user["id"])
        flash("Botones de ejemplo agregados al final de tu set.")
        st.rerun()


def community_card(item: dict, me: dict, n_buttons: int) -> None:
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
        price = float(item.get("price_usd") or 0)
        price_txt = (f" · descarga {fmt_money(price, 'USD')} (usarlo en tu SoundPad es gratis)" if price else
                     " · descarga gratis")
        st.caption(f"{item['saves']} guardados · {item['downloads']} descargas{price_txt}")

        c1, c2, c3 = st.columns([1.2, 1.2, 0.6])
        full = room_left(me, n_buttons) <= 0
        save_label = "Guardar otra vez" if item["saved_by_me"] else "Guardar"
        if c1.button(save_label, key=f"save_{item['id']}", width="stretch",
                     icon=":material/bookmark_added:" if item["saved_by_me"] else ":material/bookmark_add:",
                     type="secondary" if item["saved_by_me"] else "primary", disabled=full,
                     help="Tu plan llegó al máximo de sonidos." if full else "Agrega una copia a tu SoundPad."):
            save_from_community(me["id"], me["username"], item)
            flash(f"«{item['name']}» guardado en tu SoundPad.")
            st.rerun()

        path = BASE_DIR / item["sound_path"]
        locked = price > 0 and item["author_id"] != me["id"] and not item["bought_by_me"]
        if locked:
            if c2.button(f"Comprar {fmt_money(price, 'USD')}", key=f"buy_{item['id']}",
                         icon=":material/shopping_cart:", width="stretch"):
                checkout(f"Descarga de «{item['name']}»", price, "USD", buy_download, me["id"], item)
        elif path.exists():
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
            price = 0.0
            if plan_of(me)["sell"]:
                price = st.number_input("Precio para descargar (USD)", 0.0, 100.0, 0.0, 0.5, key=f"pub_price_{pv}",
                                        help="0 = gratis. Guardarlo y usarlo en SoundSet siempre es gratis; "
                                             "el precio es solo para descargar el archivo.")
            else:
                st.caption("Con Premium o SoundSet puedes cobrar por la descarga de tus sonidos.")
            st.caption("Al publicarlo, cualquier persona con cuenta podrá escucharlo y guardarlo en su SoundPad.")
            if st.button("Publicar en la comunidad", type="primary", icon=":material/public:"):
                if not (BASE_DIR / pb["sound_path"]).exists():
                    st.error("No se encontró el archivo de sonido de este botón. Reemplázalo en Editar botones.")
                else:
                    publish_button(me["id"], pb, desc.strip(), float(price))
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
            community_card(item, me, len(buttons))


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
        e = earnings(me["id"])
        if e["available"] or e["held"]:
            with st.container(border=True):
                st.markdown("**Ganancias**")
                for cur_, amt in e["available"].items():
                    st.markdown(f"Disponible: **{fmt_money(amt, cur_)}**")
                for cur_, amt in e["held"].items():
                    st.markdown(f"Retenido hasta entregar: {fmt_money(amt, cur_)}")
                if PAYMENT_MODE == "demo":
                    st.caption("Modo demo: los montos son de prueba y no se pueden retirar.")
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



# ================================================================== IMAGINACIÓN (Inicio)
BUBBLE_NAMES = ["Rojo", "Naranja", "Amarillo", "Verde", "Cian", "Azul", "Índigo", "Morado", "Rosa"]
BUBBLE_COLORS = [COLORS[n] for n in BUBBLE_NAMES]


def bubble_html(r: dict, big: bool = False) -> str:
    price = (f"Paga {fmt_money(r['budget'], r['currency'])}" if r["kind"] == "budget"
             else "Recibe ofertas de precio")
    return (f'<div class="ss-bubble{" big" if big else ""}" style="--b:{esc(r["color"])}">'
            f'<p>{esc(r["text"])}</p><div class="meta"><span>@{esc(r["username"])}</span>'
            f'<span class="price">{price}</span></div></div>')


def imagination_form(me: dict) -> None:
    v = st.session_state.setdefault("req_ver", 0)
    with st.container(border=True):
        text = st.text_area("¿Qué sonido imaginas?", max_chars=220, key=f"req_text_{v}",
                            placeholder="Quiero un sonido de un gato roncando y que de repente suene una explosión")
        mode = st.radio("Precio", ["Que los artistas me ofrezcan un precio", "Yo pongo cuánto pagaré"],
                        horizontal=True, key=f"req_mode_{v}", label_visibility="collapsed")
        c1, c2, c3 = st.columns([1.2, 0.8, 1.2])
        budget = currency = None
        if mode.startswith("Yo"):
            currency = c2.selectbox("Moneda", CURRENCIES, key=f"req_cur_{v}")
            clp = currency == "CLP"
            budget = c1.number_input("Pagaré", min_value=1.0, value=10500.0 if clp else 10.0,
                                     step=500.0 if clp else 1.0, format="%.0f" if clp else "%.2f",
                                     key=f"req_budget_{v}_{currency}")
        color_name = c3.selectbox("Color de la burbuja", ["Al azar", *BUBBLE_NAMES], key=f"req_color_{v}")
        if st.button("Lanzar burbuja", type="primary", icon=":material/bubble_chart:", key="req_send"):
            if len(text.strip()) < 8:
                st.error("Describe un poco más el sonido que imaginas (mínimo 8 caracteres).")
            else:
                color = random.choice(BUBBLE_COLORS) if color_name == "Al azar" else COLORS[color_name]
                create_request(me["id"], text.strip(), "budget" if budget else "offers", budget, currency, color)
                st.session_state.req_ver = v + 1
                flash("Tu burbuja ya está flotando para los artistas.", ":material/bubble_chart:")
                st.rerun()


def offer_form(me: dict, r: dict, prefix: str) -> None:
    with st.container(border=True):
        if r["kind"] == "budget":
            amount, currency = float(r["budget"]), r["currency"]
            st.markdown(f"Esta persona pagará **{fmt_money(amount, currency)}**. Si confirmas, ella decide si te lo encarga.")
        else:
            c1, c2 = st.columns([2, 1])
            currency = c2.selectbox("Moneda", CURRENCIES, key=f"{prefix}_cur_{r['id']}")
            clp = currency == "CLP"
            amount = c1.number_input("¿Cuánto cobras?", min_value=1.0, value=5000.0 if clp else 5.0,
                                     step=500.0 if clp else 1.0, format="%.0f" if clp else "%.2f",
                                     key=f"{prefix}_amt_{r['id']}_{currency}")
        msg = st.text_input("Mensaje para la persona (opcional)", max_chars=160, key=f"{prefix}_msg_{r['id']}",
                            placeholder="Ej: lo tengo listo en 2 días, con 3 versiones")
        b1, b2 = st.columns(2)
        label = "Confirmar: lo hago" if r["kind"] == "budget" else "Enviar oferta"
        if b1.button(label, type="primary", key=f"{prefix}_send_{r['id']}", icon=":material/send:", width="stretch"):
            create_offer(me["id"], r["id"], float(amount), currency, msg.strip())
            st.session_state.pop("offer_for", None)
            flash("Oferta enviada. Verás la respuesta en «Mis trabajos».", ":material/send:")
            st.rerun()
        if b2.button("Cancelar", key=f"{prefix}_cancel_{r['id']}", width="stretch"):
            st.session_state.pop("offer_for", None)
            st.rerun()


@st.fragment(run_every=15)
def bubble_feed(me: dict) -> None:
    """Una burbuja grande que cambia sola cada 15 segundos."""
    reqs = [r for r in open_requests(me["id"]) if not r["offered"]]
    if not reqs:
        st.info("No hay burbujas nuevas por ahora. Vuelve en un rato.", icon=":material/bubble_chart:")
        return
    frozen = st.session_state.get("offer_for")
    idx = next((i for i, x in enumerate(reqs) if x["id"] == frozen), None)
    if idx is None:
        idx = (int(time.time() // 15) + st.session_state.get("bubble_offset", 0)) % len(reqs)
    r = reqs[idx]
    st.markdown(bubble_html(r, big=True), unsafe_allow_html=True)
    st.markdown(f'<p class="ss-muted" style="text-align:center">Burbuja {idx + 1} de {len(reqs)} · '
                'cambia sola cada 15 segundos</p>', unsafe_allow_html=True)
    if frozen == r["id"]:
        offer_form(me, r, "feat")
        return
    _, c1, c2, _ = st.columns([1, 1.2, 1, 1])
    if c1.button("Lo hago" if r["kind"] == "budget" else "Ofrecer ayuda", type="primary",
                 key=f"feat_take_{r['id']}", icon=":material/handshake:", width="stretch"):
        st.session_state.offer_for = r["id"]
        st.rerun(scope="fragment")
    if c2.button("Pasar", key=f"feat_pass_{r['id']}", icon=":material/skip_next:", width="stretch"):
        st.session_state.bubble_offset = st.session_state.get("bubble_offset", 0) + 1
        st.rerun(scope="fragment")


def all_bubbles(me: dict) -> None:
    reqs = open_requests(me["id"])
    if not reqs:
        st.info("No hay burbujas abiertas por ahora.")
        return
    cols = st.columns(3, gap="medium")
    for i, r in enumerate(reqs):
        with cols[i % 3]:
            st.markdown(bubble_html(r), unsafe_allow_html=True)
            if st.session_state.get("offer_for") == r["id"]:
                offer_form(me, r, "all")
            elif r["offered"]:
                st.button("Oferta enviada", key=f"all_sent_{r['id']}", disabled=True, width="stretch",
                          icon=":material/check:")
            elif st.button("Lo hago" if r["kind"] == "budget" else "Ofrecer ayuda", key=f"all_take_{r['id']}",
                           icon=":material/handshake:", width="stretch"):
                st.session_state.offer_for = r["id"]
                st.rerun()


def my_requests_view(me: dict, n_buttons: int) -> None:
    reqs = my_requests(me["id"])
    if not reqs:
        st.info("Aún no lanzas burbujas. Describe arriba el sonido que imaginas.")
        return
    for r in reqs:
        with st.container(border=True):
            c1, c2 = st.columns([1, 1.5], gap="large")
            c1.markdown(bubble_html(r), unsafe_allow_html=True)
            with c2:
                if r["status"] == "open":
                    offers = request_offers(r["id"])
                    if not offers:
                        st.caption("Esperando que algún artista tome tu burbuja…")
                    for o in offers:
                        st.markdown(
                            f'<div class="ss-author">{avatar_html(o, 28)}<span><b>{esc(o.get("display_name") or o["username"])}</b> '
                            f'<span class="ss-muted">@{esc(o["username"])}</span> · '
                            f'<b style="color:#ff8a2a">{fmt_money(o["amount"], o["currency"])}</b></span></div>',
                            unsafe_allow_html=True)
                        if o["message"]:
                            st.caption(o["message"])
                        a, b = st.columns(2)
                        if a.button("Aceptar y pagar", key=f"acc_{o['id']}", type="primary", width="stretch",
                                    icon=":material/check_circle:"):
                            checkout(f"Encargo a @{o['username']}", o["amount"], o["currency"],
                                     accept_offer, me["id"], o, r)
                        if b.button("Rechazar", key=f"rej_{o['id']}", width="stretch", icon=":material/close:"):
                            reject_offer(me["id"], o["id"])
                            flash("Oferta rechazada.", ":material/close:")
                            st.rerun()
                    if st.button("Quitar mi burbuja", key=f"close_{r['id']}", type="tertiary",
                                 icon=":material/delete:"):
                        close_request(me["id"], r["id"])
                        flash("Burbuja retirada.", ":material/delete:")
                        st.rerun()
                elif r["status"] == "in_progress":
                    st.info(f"@{r['artist_username']} está creando tu sonido. Pagaste "
                            f"{fmt_money(r['agreed_amount'], r['agreed_currency'])}; el pago queda retenido hasta "
                            "la entrega.", icon=":material/hourglass_top:")
                elif r["status"] == "delivered":
                    st.success(f"@{r['artist_username']} entregó tu sonido.", icon=":material/celebration:")
                    path = BASE_DIR / (r["sound_path"] or "")
                    if path.exists():
                        show_html(build_pad_html([button_payload({"id": f"r{r['id']}", "name": "Escuchar",
                                                                  "icon": "wave", "color": r["color"],
                                                                  "sound_path": r["sound_path"]})], "mini",
                                                 st.session_state.stop_nonce), 112)
                        a, b = st.columns(2)
                        if a.button("Guardar en mi set", key=f"rsave_{r['id']}", type="primary", width="stretch",
                                    icon=":material/bookmark_add:", disabled=room_left(me, n_buttons) <= 0):
                            save_request_sound(me["id"], me["username"], r)
                            flash("Sonido guardado en tu SoundPad.")
                            st.rerun()
                        b.download_button("Descargar", data=path.read_bytes(), key=f"rdl_{r['id']}",
                                          file_name=f"{slugify(r['text'][:30])}{path.suffix}", width="stretch",
                                          icon=":material/download:")


def jobs_view(me: dict) -> None:
    jobs = artist_jobs(me["id"])
    if not jobs:
        st.info("Aún no tomas burbujas. Cuando ofrezcas ayuda, tus trabajos aparecerán aquí.")
        return
    for j in jobs:
        with st.container(border=True):
            c1, c2 = st.columns([1, 1.5], gap="large")
            c1.markdown(bubble_html(j), unsafe_allow_html=True)
            with c2:
                money = fmt_money(j["amount"], j["currency"])
                if j["offer_status"] == "pending":
                    st.caption(f"Ofreciste hacerlo por {money}. Esperando respuesta de @{j['username']}.")
                    if st.button("Retirar oferta", key=f"wd_{j['offer_id']}", type="tertiary", icon=":material/undo:"):
                        withdraw_offer(me["id"], j["offer_id"])
                        st.rerun()
                elif j["offer_status"] == "rejected":
                    st.caption(f"@{j['username']} eligió otra opción o retiró su burbuja.")
                elif j["status"] == "in_progress":
                    st.success(f"¡@{j['username']} aceptó! Pagó {money}; lo recibes al entregar el sonido.",
                               icon=":material/handshake:")
                    up = st.file_uploader("Sube el sonido terminado (MP3 o WAV)", type=["mp3", "wav"],
                                          key=f"deliv_{j['id']}")
                    if st.button("Entregar sonido", key=f"deliv_btn_{j['id']}", type="primary",
                                 icon=":material/send:", disabled=up is None):
                        deliver_request(me["id"], j["id"], up)
                        flash(f"Sonido entregado. Se liberó tu pago de {money}.", ":material/payments:")
                        st.rerun()
                else:
                    st.caption(f"Entregado. Pago liberado: {money}.")


def page_home(me: dict, buttons: list[dict]) -> None:
    dots = "".join(f'<i style="background:{c}"></i>' for c in BUBBLE_COLORS)
    st.markdown(f'<div class="ss-hero"><div class="ss-dots">{dots}</div><h1>Imaginación</h1>'
                '<p class="ss-muted">Describe el sonido que imaginas y deja que los artistas de SoundSet lo creen '
                'para ti.</p></div>', unsafe_allow_html=True)
    imagination_form(me)

    artist = plan_of(me)["artist"]
    n_offers = pending_offers_count(me["id"])
    labels = ["Burbujas", f"Mis pedidos ({n_offers})" if n_offers else "Mis pedidos"]
    if artist:
        labels.append("Mis trabajos")
    tabs = st.tabs(labels)
    with tabs[0]:
        if artist:
            if st.toggle("Ver todas las burbujas", key="bubbles_all"):
                all_bubbles(me)
            else:
                bubble_feed(me)
        else:
            n = len(open_requests(me["id"]))
            st.markdown(
                f'<div class="ss-bubble big" style="--b:#8e24aa"><p>Hay {n} burbuja(s) esperando artistas</p>'
                '<div class="meta"><span>Con el plan SoundSet activas el modo artista: ves los pedidos y cobras '
                'por crear sonidos.</span></div></div>', unsafe_allow_html=True)
            st.button("Activar modo artista", key="home_upgrade", icon=":material/workspace_premium:",
                      on_click=go_to, args=(PAGE_PLANS,), type="primary")
    with tabs[1]:
        my_requests_view(me, len(buttons))
    if artist:
        with tabs[2]:
            jobs_view(me)


# ================================================================== PLANES
PLAN_FEATURES = {
    "free": ["Hasta 20 sonidos", "Calidad intermedia: 352 kbps", "Publica gratis en Comunidad",
             "Pide sonidos en Imaginación"],
    "premium": ["Hasta 100 sonidos", "Calidad CD: 1411 kbps", "Token para OBS y puntos del canal",
                "Cobra por la descarga de tus sonidos"],
    "soundset": ["Sonidos ilimitados", "Calidad CD: 1411 kbps", "Todo lo de Premium: OBS y ventas",
                 "Modo artista: cobra por crear sonidos en Imaginación"],
}
PLAN_STYLE = {"free": ("", "Para empezar"), "premium": ("gold", "Para streamers"),
              "soundset": ("rgb", "Para artistas y creadores")}


def page_plans(me: dict) -> None:
    st.markdown("## Planes")
    cur = active_plan(me)
    if cur != "free":
        exp = date.fromisoformat(me["plan_expires"]).strftime("%d-%m-%Y")
        st.caption(f"Tu plan {PLANS[cur]['name']} está activo hasta el {exp}.")
    cols = st.columns(3, gap="small")
    for col, (key, p) in zip(cols, PLANS.items()):
        with col:
            cls, tagline = PLAN_STYLE[key]
            price = "Gratis" if not p["price"] else f"US${p['price']}<small> / año</small>"
            items = "".join(f"<li>{esc(f)}</li>" for f in PLAN_FEATURES[key])
            badge = '<span class="cur">Tu plan</span>' if key == cur else ""
            cls += " has-cur" if key == cur else ""
            st.markdown(f'<div class="ss-plan {cls}">{badge}<h3>{p["name"]}</h3><div class="tg">{tagline}</div>'
                        f'<div class="pr">{price}</div><ul>{items}</ul></div>', unsafe_allow_html=True)
            if key == cur:
                if p["price"] and st.button("Renovar 1 año", key=f"plan_{key}", width="stretch",
                                            icon=":material/autorenew:"):
                    checkout(f"Renovación plan {p['name']} (1 año)", p["price"], "USD", buy_plan, me["id"], key)
                elif not p["price"]:
                    st.button("Tu plan actual", key=f"plan_{key}", disabled=True, width="stretch")
            elif key == "free":
                if st.button("Volver a Estándar", key=f"plan_{key}", width="stretch",
                             help="No se borran tus sonidos, pero no podrás agregar más sobre el límite."):
                    cancel_plan(me["id"])
                    flash("Volviste al plan Estándar.")
                    st.rerun()
            elif st.button(f"Elegir {p['name']}", key=f"plan_{key}", type="primary", width="stretch",
                           icon=":material/workspace_premium:"):
                checkout(f"Plan {p['name']} (1 año)", p["price"], "USD", buy_plan, me["id"], key)
    if PAYMENT_MODE == "demo":
        st.caption("Los pagos están en modo demo: se registran, pero no se cobra dinero real.")

    pays = my_payments(me["id"])
    if pays:
        with st.expander("Historial de pagos", icon=":material/receipt_long:"):
            status_txt = {"paid": "Pagado", "held": "Retenido hasta la entrega", "released": "Liberado"}
            st.dataframe(
                [{"Fecha": p["created_at"][:10], "Detalle": p["description"],
                  "Monto": fmt_money(p["amount"], p["currency"]),
                  "Tipo": "Recibido" if p["payee_id"] == me["id"] else "Pagado",
                  "Estado": status_txt.get(p["status"], p["status"])} for p in pays],
                hide_index=True, width="stretch")


# ================================================================== STREAM / OBS
def overlay_url(token: str) -> str:
    from urllib.parse import urlsplit
    raw = getattr(st.context, "url", None) or "http://localhost:8501/"
    parts = urlsplit(raw)
    return f"{parts.scheme}://{parts.netloc}/?obs={token}"


def page_obs(me: dict, buttons: list[dict]) -> None:
    st.markdown("## Stream / OBS")
    if not plan_of(me)["obs"]:
        st.info("Con Premium o SoundSet obtienes un token para usar tus sonidos en OBS y activarlos con los "
                "puntos del canal de Twitch.", icon=":material/lock:")
        st.button("Ver planes", key="obs_upgrade", type="primary", icon=":material/workspace_premium:",
                  on_click=go_to, args=(PAGE_PLANS,))
        return

    st.markdown("#### 1. Tu enlace para OBS")
    if not me.get("obs_token"):
        if st.button("Crear mi token", type="primary", icon=":material/key:"):
            ensure_obs_token(me["id"])
            st.rerun()
        return
    url = overlay_url(me["obs_token"])
    st.code(url, language=None)
    st.caption("En OBS: Fuentes → + → Navegador. Pega este enlace (ancho 800, alto 250) y marca «Controlar audio "
               "mediante OBS». No compartas el enlace: quien lo tenga puede abrir tu overlay. Si cambias tus "
               "botones, actualiza la fuente en OBS.")
    c1, c2, _ = st.columns([1, 1, 2])
    c1.link_button("Probar overlay", url + "&test=1", icon=":material/open_in_new:", width="stretch")
    if c2.button("Regenerar token", icon=":material/refresh:", width="stretch",
                 help="El enlace anterior deja de funcionar."):
        ensure_obs_token(me["id"], regenerate=True)
        flash("Token nuevo creado. Actualiza el enlace en OBS.", ":material/key:")
        st.rerun()

    st.markdown("#### 2. Conecta tu canal de Twitch")
    with st.form("twitch_form"):
        channel = st.text_input("Nombre de tu canal", me.get("twitch_channel") or "", placeholder="tu_canal")
        a, b = st.columns(2)
        client_id = a.text_input("Client ID de Twitch", me.get("twitch_client_id") or "")
        token = b.text_input("Token de acceso", me.get("twitch_token") or "", type="password",
                             help="Token de usuario con el permiso channel:read:redemptions.")
        c, d, e = st.columns([1.2, 1, 1.2])
        chat_on = c.toggle("Activar también comandos de chat", bool(me.get("chat_enabled")))
        prefix = d.text_input("Comando", me.get("chat_prefix") or "!sonido", max_chars=20)
        cooldown = e.slider("Espera entre usos del mismo sonido (s)", 0, 60, int(me.get("obs_cooldown") or 5))
        if st.form_submit_button("Guardar conexión", type="primary", icon=":material/save:"):
            update_stream_settings(me["id"], twitch_channel=channel.strip().lstrip("#"),
                                   twitch_client_id=client_id.strip(), twitch_token=token.strip(),
                                   chat_enabled=int(chat_on), chat_prefix=prefix.strip() or "!sonido",
                                   obs_cooldown=cooldown)
            flash("Conexión con Twitch guardada. Actualiza la fuente en OBS.", ":material/cast:")
            st.rerun()
    with st.expander("¿Cómo consigo el Client ID y el token?", icon=":material/help:"):
        st.markdown(
            "1. Entra a **dev.twitch.tv/console**, crea una aplicación y copia su **Client ID**.\n"
            "2. Genera un **token de usuario** con el permiso `channel:read:redemptions`, por ejemplo con "
            "Twitch CLI: `twitch token -u -s channel:read:redemptions`.\n"
            "3. Pega ambos datos aquí. Los tokens de Twitch vencen; si el overlay deja de reaccionar, genera uno "
            "nuevo.\n\n"
            "Los comandos de chat no necesitan token: cualquiera en tu chat puede escribir "
            f"`{esc(me.get('chat_prefix') or '!sonido')} nombre del botón`.")

    st.markdown("#### 3. Crea las recompensas con el mismo nombre")
    st.caption("En Twitch → Panel de creador → Puntos del canal, crea una recompensa por cada sonido con el "
               "nombre exacto del botón (no importan mayúsculas ni tildes). Al canjearla, el sonido suena en OBS.")
    prefix = me.get("chat_prefix") or "!sonido"
    st.dataframe([{"Botón / recompensa": b["name"], "Comando de chat": f"{prefix} {b['name']}"} for b in buttons],
                 hide_index=True, width="stretch")


OVERLAY_CSS = """
<style>
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: transparent !important; }
[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="stSidebarCollapsedControl"], footer { display: none !important; }
.block-container { padding: 0 !important; max-width: none !important; }
</style>
"""


def render_overlay(token: str, test: bool) -> None:
    st.markdown(OVERLAY_CSS, unsafe_allow_html=True)
    u = user_by_obs_token(token)
    if not u or not plan_of(u)["obs"]:
        st.markdown('<p style="color:#fff;font-family:sans-serif;padding:12px">Este enlace de OBS no es válido o '
                    'el plan venció.</p>', unsafe_allow_html=True)
        return
    payload = [button_payload(b) for b in get_buttons(u["id"])]
    cfg = {"channel": u.get("twitch_channel") or "", "clientId": u.get("twitch_client_id") or "",
           "token": u.get("twitch_token") or "", "chat": bool(u.get("chat_enabled")),
           "prefix": u.get("chat_prefix") or "!sonido", "cooldown": int(u.get("obs_cooldown") or 0), "test": test}
    show_html(build_overlay_html(payload, cfg), 260)


# ================================================================== MAIN
def main() -> None:
    ensure_default_sounds()
    init_db()
    obs = st.query_params.get("obs")
    if obs:  # página que se carga dentro de OBS
        render_overlay(obs, st.query_params.get("test") == "1")
        return
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

    if page == PAGE_HOME:
        page_home(me, buttons)
    elif page == PAGE_COMMUNITY:
        page_community(me, buttons)
    elif page == PAGE_ADD:
        page_add(me, len(buttons))
    elif page == PAGE_EDIT:
        page_edit(me, buttons)
    elif page == PAGE_PROFILE:
        page_profile(me)
    elif page == PAGE_OBS:
        page_obs(me, buttons)
    elif page == PAGE_PLANS:
        page_plans(me)
    else:
        page_pad(me, buttons)


main()
