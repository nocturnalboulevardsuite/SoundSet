"""
SoundSet — SoundPad personalizable hecho con Streamlit.

Ejecutar:
    pip install -r requirements.txt
    streamlit run app.py

Estructura:
    app.py
    requirements.txt
    sounds/
        default/   -> 10 sonidos de ejemplo (se generan solos si no existen)
        users/     -> sonidos que sube cada usuario (una carpeta por usuario)
    soundset.db    -> base de datos SQLite (se crea sola)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import tempfile
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

# ------------------------------------------------------------------ rutas y constantes
BASE_DIR = Path(__file__).resolve().parent
SOUNDS_DIR = BASE_DIR / "sounds"
DEFAULT_DIR = SOUNDS_DIR / "default"
USER_SOUNDS_DIR = SOUNDS_DIR / "users"
DB_PATH = BASE_DIR / "soundset.db"
SR = 22050  # frecuencia de muestreo de los sonidos de ejemplo
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")

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

st.set_page_config(
    page_title="SoundSet",
    page_icon="🎛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


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


# clave, nombre, emoji, color, generador
DEFAULT_SOUNDS = [
    ("alarma", "Alarma", "🚨", COLORS["Rojo"], snd_alarma),
    ("bocina", "Bocina", "📯", COLORS["Naranja"], snd_bocina),
    ("laser", "Láser", "⚡", COLORS["Azul"], snd_laser),
    ("moneda", "Moneda", "🪙", COLORS["Amarillo"], snd_moneda),
    ("explosion", "Explosión", "💥", COLORS["Negro"], snd_explosion),
    ("aplausos", "Aplausos", "👏", COLORS["Verde"], snd_aplausos),
    ("campana", "Campana", "🔔", COLORS["Morado"], snd_campana),
    ("boing", "Boing", "🤪", COLORS["Rosa"], snd_boing),
    ("tambor", "Tambor", "🥁", COLORS["Cian"], snd_tambor),
    ("sirena", "Sirena", "🚓", COLORS["Índigo"], snd_sirena),
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
    DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    USER_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
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
                emoji TEXT DEFAULT '🔊',
                image_b64 TEXT,
                image_mime TEXT,
                color TEXT NOT NULL DEFAULT '#ff6d00',
                sound_path TEXT NOT NULL,
                start_s REAL NOT NULL DEFAULT 0,
                end_s REAL,
                volume REAL NOT NULL DEFAULT 1.0,
                position INTEGER NOT NULL DEFAULT 0
            );
            """
        )
    return True


# ------------------------------------------------------------------ usuarios
def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000).hex()


def create_user(username: str, password: str) -> int | None:
    salt = secrets.token_hex(16)
    try:
        with db() as con:
            cur = con.execute(
                "INSERT INTO users(username, pw_hash, salt) VALUES (?, ?, ?)",
                (username, _hash(password, salt), salt),
            )
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    seed_default_buttons(uid)  # cada cuenta nueva parte con los 10 botones de ejemplo
    return uid


def verify_user(username: str, password: str) -> dict | None:
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row and hmac.compare_digest(row["pw_hash"], _hash(password, row["salt"])):
        return {"id": row["id"], "username": row["username"]}
    return None


# ------------------------------------------------------------------ botones
BUTTON_FIELDS = {"name", "emoji", "image_b64", "image_mime", "color", "sound_path",
                 "start_s", "end_s", "volume", "position"}


def _next_position(con, uid: int) -> int:
    return con.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM buttons WHERE user_id = ?", (uid,)
    ).fetchone()[0]


def seed_default_buttons(uid: int) -> None:
    with db() as con:
        pos = _next_position(con, uid)
        for i, (key, name, emoji, color, _) in enumerate(DEFAULT_SOUNDS):
            con.execute(
                "INSERT INTO buttons(user_id, name, emoji, color, sound_path, position) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, name, emoji, color, f"sounds/default/{key}.wav", pos + i),
            )


def get_buttons(uid: int) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT * FROM buttons WHERE user_id = ? ORDER BY position, id", (uid,)
        ).fetchall()
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
        con.execute(f"UPDATE buttons SET {sets} WHERE id = ? AND user_id = ?",
                    (*fields.values(), bid, uid))


def cleanup_sound(sound_path: str) -> None:
    """Borra un archivo subido por un usuario si ya ningún botón lo usa."""
    if not sound_path.startswith("sounds/users/"):
        return  # nunca se borran los sonidos de ejemplo
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


# ================================================================== ARCHIVOS DE AUDIO
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


def read_image(uploaded) -> tuple[str | None, str | None]:
    if uploaded is None:
        return None, None
    data = uploaded.getvalue()
    if len(data) > 2 * 1024 * 1024:
        st.warning("La imagen pesa más de 2 MB; el pad cargará más lento. Usa una imagen más liviana si puedes.")
    return base64.b64encode(data).decode(), (uploaded.type or "image/png")


# ================================================================== PAD (HTML + Web Audio)
PAD_TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--line:#2b2420;--orange:#ff6a00;--orange2:#ffa04d;--text:#f4efe9;--muted:#9a8f86;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:transparent;color:var(--text);font-family:Outfit,system-ui,sans-serif;user-select:none;-webkit-user-select:none;padding:4px 4px 12px}
.bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;background:linear-gradient(120deg,#2a1608,#1a1613 55%);
 border:1px solid #3a2414;border-radius:18px;padding:12px 16px;margin-bottom:16px;position:sticky;top:0;z-index:5;
 box-shadow:0 10px 28px rgba(0,0,0,.45)}
.live{display:flex;align-items:center;gap:9px;font-weight:600;font-size:15px;min-width:120px}
.dot{width:11px;height:11px;border-radius:50%;background:#3d332c;transition:.2s}
.dot.on{background:var(--orange);box-shadow:0 0 14px var(--orange)}
.vol{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px}
input[type=range]{accent-color:var(--orange);width:140px}
.spacer{flex:1}
.hint{color:var(--muted);font-size:13px}
.stop{background:linear-gradient(135deg,#ff3b30,#b3121a);color:#fff;border:none;border-radius:12px;padding:10px 18px;
 font:700 15px Outfit,sans-serif;cursor:pointer;box-shadow:0 6px 18px rgba(255,59,48,.35);transition:transform .08s}
.stop:active{transform:scale(.94)}
.stop:focus-visible,.pad:focus-visible{outline:3px solid var(--orange2);outline-offset:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px}
.pad{--c:#ff6a00;position:relative;aspect-ratio:1/1;border-radius:22px;cursor:pointer;overflow:hidden;
 border:1px solid rgba(255,255,255,.08);
 background:radial-gradient(130% 100% at 25% 0%, color-mix(in srgb,var(--c) 88%,#fff 12%) 0%, var(--c) 35%, color-mix(in srgb,var(--c) 45%,#000) 100%);
 box-shadow:0 10px 22px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.22), inset 0 -6px 0 rgba(0,0,0,.25);
 display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;
 transition:transform .06s ease, box-shadow .2s}
.pad:hover{box-shadow:0 0 0 2px var(--orange), 0 12px 28px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.22)}
.pad.hit{transform:scale(.92) translateY(2px)}
.pad.playing{box-shadow:0 0 0 2px #fff, 0 0 30px color-mix(in srgb,var(--c) 75%,#fff)}
.pad.missing{filter:grayscale(.8);opacity:.5}
.icon{width:54%;aspect-ratio:1/1;border-radius:50%;background:rgba(0,0,0,.24);display:flex;align-items:center;
 justify-content:center;font-size:clamp(28px,4vw,50px);overflow:hidden;box-shadow:inset 0 3px 10px rgba(0,0,0,.4);pointer-events:none}
.icon img{width:100%;height:100%;object-fit:cover}
.name{font-weight:700;font-size:15px;text-shadow:0 1px 4px rgba(0,0,0,.7);max-width:90%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.key{position:absolute;top:9px;left:10px;font-size:12px;font-weight:700;background:rgba(0,0,0,.38);padding:1px 8px;border-radius:8px;color:rgba(255,255,255,.88)}
.count{position:absolute;top:9px;right:10px;font-size:12px;font-weight:800;background:#fff;color:#1a1613;padding:1px 8px;border-radius:8px;opacity:0;transform:scale(.5);transition:.12s}
.count.on{opacity:1;transform:scale(1)}
.prog{position:absolute;left:0;bottom:0;height:5px;width:0;background:rgba(255,255,255,.9)}
.ripple{position:absolute;width:60px;height:60px;margin:-30px 0 0 -30px;border-radius:50%;background:rgba(255,255,255,.5);transform:scale(0);animation:rip .45s ease-out forwards;pointer-events:none}
@keyframes rip{to{transform:scale(5);opacity:0}}
.empty{border:2px dashed #3a2a20;border-radius:20px;padding:40px;text-align:center;color:var(--muted);font-size:16px}
.preview{display:grid;grid-template-columns:170px 1fr;gap:18px;align-items:center}
.wave{width:100%;height:120px;border-radius:14px;background:#161210;border:1px solid var(--line);display:block}
.pinfo{display:flex;align-items:center;gap:12px;margin-top:10px;color:var(--muted);font-size:14px;flex-wrap:wrap}
.pinfo .stop{padding:7px 14px;font-size:13px}
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
      <button class="stop" id="stopBtn">⏹ Detener todos los sonidos</button>
    </div>
    <div class="grid" id="grid"></div>`;
  document.getElementById('vol').addEventListener('input', e => master.gain.value = parseFloat(e.target.value));
  const grid = document.getElementById('grid');
  if (!DATA.length) grid.outerHTML = '<div class="empty">Tu set está vacío. Abre <b>➕ Agregar botón</b> en el menú para crear el primero.</div>';
  DATA.forEach((b,i) => { const el = makePad(b,i); pads.push(el); grid.appendChild(el); });
} else {
  root.innerHTML = `
    <div class="preview">
      <div id="one"></div>
      <div><canvas class="wave" id="wave"></canvas>
        <div class="pinfo"><span id="info">Cargando audio…</span><span class="spacer"></span>
        <button class="stop" id="stopBtn">⏹ Detener</button></div></div>
    </div>`;
  if (DATA.length) { const el = makePad(DATA[0], -1); pads.push(el); document.getElementById('one').appendChild(el); drawWave(DATA[0]); }
}
document.getElementById('stopBtn').addEventListener('click', stopAll);

function makePad(b, i){
  const el = document.createElement('div');
  el.className = 'pad' + (b.audio ? '' : ' missing');
  el.tabIndex = 0;
  el.style.setProperty('--c', b.color);
  el.innerHTML = (i >= 0 && i < KEYS.length ? `<span class="key">${KEYS[i]}</span>` : '') +
    '<span class="count"></span><div class="icon"></div><div class="name"></div><div class="prog"></div>';
  const icon = el.querySelector('.icon');
  if (b.img) { const im = document.createElement('img'); im.src = b.img; im.alt = ''; icon.appendChild(im); }
  else icon.textContent = b.emoji || '🔊';
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
  if (c) c.textContent = n + (n === 1 ? ' sonando' : ' sonando');
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
        "emoji": b.get("emoji") or "🔊",
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


def show_html(html: str, height: int) -> None:
    """Usa st.iframe en Streamlit nuevo y components.html en versiones anteriores."""
    if hasattr(st, "iframe"):
        st.iframe(html, height="content")
    else:
        components.html(html, height=height, scrolling=True)


def render_preview(b: dict, audio_b64: str | None) -> None:
    show_html(build_pad_html([button_payload(b, audio_b64)], "preview", st.session_state.stop_nonce), 215)


# ================================================================== ESTILO DE LA APP
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap');
.stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp h1, .stApp h2, .stApp h3, .stApp li {
  font-family: 'Outfit', system-ui, sans-serif;
}
.stApp { background: radial-gradient(1100px 520px at 12% -12%, #3a1a06 0%, #0f0d0c 58%) fixed; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: #15110f; border-right: 1px solid #2b2420; }
.block-container { padding-top: 2.2rem; max-width: 1320px; }
.stApp h2 { font-weight: 800; letter-spacing: -.5px; }
.stButton > button, .stFormSubmitButton > button {
  border-radius: 12px; font-weight: 600; border: 1px solid #3a2e27; transition: .15s;
}
.stButton > button:hover, .stFormSubmitButton > button:hover { border-color: #ff6a00; color: #ffa04d; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: linear-gradient(135deg, #ff7a1a, #d94f00); border: none; color: #fff;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover { color: #fff; filter: brightness(1.08); }
.st-key-stop_all button { background: linear-gradient(135deg, #ff3b30, #b3121a) !important; color: #fff !important; border: none !important; }
.ss-logo { font-weight: 800; letter-spacing: -1px; line-height: 1.1; color: #f4efe9; }
.ss-logo b { color: #ff7a1a; }
.ss-sub { color: #9a8f86; margin: .2rem 0 1.2rem; }
.ss-muted { color: #9a8f86; font-size: .92rem; }
div[data-baseweb="tab-list"] button[aria-selected="true"] p { color: #ffa04d; }
#MainMenu, footer { visibility: hidden; }
</style>
"""


def logo(size: str = "2rem") -> None:
    st.markdown(f'<div class="ss-logo" style="font-size:{size}">🎛️ Sound<b>Set</b></div>',
                unsafe_allow_html=True)


def flash(msg: str, icon: str = "✅") -> None:
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


# ================================================================== PÁGINAS
def page_auth() -> None:
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.markdown("<div style='height:7vh'></div>", unsafe_allow_html=True)
        logo("3rem")
        st.markdown('<p class="ss-sub">Tu SoundPad en la web. Cada cuenta tiene su propio set de botones.</p>',
                    unsafe_allow_html=True)
        t_login, t_reg = st.tabs(["Iniciar sesión", "Crear cuenta"])

        with t_login:
            with st.form("login_form"):
                u = st.text_input("Usuario")
                p = st.text_input("Contraseña", type="password")
                ok = st.form_submit_button("Entrar", type="primary", width="stretch")
            if ok:
                user = verify_user(u.strip(), p)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    st.error("Usuario o contraseña incorrectos.")

        with t_reg:
            with st.form("register_form"):
                u = st.text_input("Usuario", help="3 a 20 caracteres: letras, números, _ o -")
                p = st.text_input("Contraseña", type="password", help="Mínimo 4 caracteres")
                p2 = st.text_input("Repite la contraseña", type="password")
                ok = st.form_submit_button("Crear cuenta", type="primary", width="stretch")
            if ok:
                u = u.strip()
                if not USERNAME_RE.match(u):
                    st.error("El usuario debe tener de 3 a 20 caracteres (letras, números, _ o -).")
                elif len(p) < 4:
                    st.error("La contraseña debe tener al menos 4 caracteres.")
                elif p != p2:
                    st.error("Las contraseñas no coinciden.")
                else:
                    uid = create_user(u, p)
                    if uid is None:
                        st.error("Ese usuario ya existe. Elige otro nombre.")
                    else:
                        st.session_state.user = {"id": uid, "username": u}
                        flash(f"Cuenta creada. ¡Bienvenido/a, {u}! Te dejamos 10 botones de ejemplo.", "🎉")
                        st.rerun()


def sidebar(user: dict, n_buttons: int) -> str:
    with st.sidebar:
        logo("1.7rem")
        st.markdown(
            f"Hola, **{user['username']}**  \n<span class='ss-muted'>{n_buttons} botones en tu set</span>",
            unsafe_allow_html=True,
        )
        st.divider()
        page = st.radio("Menú", ["🎛️ Mi SoundPad", "➕ Agregar botón", "✏️ Editar botones"],
                        key="page", label_visibility="collapsed")
        st.divider()
        if st.button("⏹ Detener todos los sonidos", key="stop_all", width="stretch"):
            st.session_state.stop_nonce += 1
        st.caption("Dentro del pad también puedes presionar **Esc**.")
        st.divider()
        if st.button("Cerrar sesión", width="stretch"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()
    return page


def page_pad(buttons: list[dict]) -> None:
    st.markdown("## Mi SoundPad")
    st.markdown('<p class="ss-muted">Aprieta los botones todas las veces que quieras: los sonidos se acumulan. '
                'Para personalizarlos usa <b>✏️ Editar botones</b>.</p>', unsafe_allow_html=True)
    payload = [button_payload(b) for b in buttons]
    rows = max(1, math.ceil(len(buttons) / 5))
    show_html(build_pad_html(payload, "pad", st.session_state.stop_nonce), 110 + rows * 185)


def page_add(user: dict) -> None:
    v = st.session_state.setdefault("add_ver", 0)
    st.markdown("## Agregar botón")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        name = st.text_input("Nombre del botón", max_chars=24, key=f"add_name_{v}", placeholder="Ej: Risa malvada")
        emoji = st.text_input("Emoji (se muestra si no subes imagen)", value="🔊", max_chars=8, key=f"add_emoji_{v}")
        img = st.file_uploader("Imagen de referencia (opcional)", type=["png", "jpg", "jpeg", "webp", "gif"],
                               key=f"add_img_{v}")
        color = color_selector(f"add_{v}", COLORS["Naranja"])
    with c2:
        snd = st.file_uploader("Sonido (MP3 o WAV)", type=["mp3", "wav"], key=f"add_snd_{v}")
        volume = st.slider("Volumen del botón", 0.0, 2.0, 1.0, 0.05, key=f"add_vol_{v}")
        start, end, dur, audio_b64 = 0.0, None, None, None
        if snd is not None:
            data = snd.getvalue()
            audio_b64 = base64.b64encode(data).decode()
            dur = duration_from_bytes(data, Path(snd.name).suffix.lower()) or 30.0
            dur = round(dur, 2)
            start, end = st.slider("✂️ Recorte: inicio y fin (segundos)", 0.0, dur, (0.0, dur), step=0.01,
                                   key=f"add_trim_{v}_{snd.name}_{snd.size}")

    if snd is not None:
        st.markdown("#### Vista previa")
        img_b64, img_mime = (base64.b64encode(img.getvalue()).decode(), img.type) if img else (None, None)
        render_preview({"name": name or "Nuevo botón", "emoji": emoji, "color": color, "image_b64": img_b64,
                        "image_mime": img_mime, "start_s": start, "end_s": end, "volume": volume}, audio_b64)

    if st.button("💾 Guardar botón", type="primary"):
        if not name.strip():
            st.error("Escribe un nombre para el botón.")
        elif snd is None:
            st.error("Sube un archivo MP3 o WAV para el botón.")
        else:
            img_b64, img_mime = read_image(img)
            add_button(
                user["id"], name=name.strip(), emoji=emoji.strip() or "🔊", image_b64=img_b64,
                image_mime=img_mime, color=color, sound_path=save_uploaded_sound(user["username"], snd),
                start_s=float(start), end_s=None if (end is None or end >= dur - 0.01) else float(end),
                volume=float(volume),
            )
            st.session_state.add_ver = v + 1
            flash(f"Botón «{name.strip()}» guardado.")
            st.rerun()


def page_edit(user: dict, buttons: list[dict]) -> None:
    v = st.session_state.setdefault("edit_ver", 0)
    st.markdown("## Editar botones")
    if not buttons:
        st.info("Tu set está vacío. Agrega un botón nuevo o recupera los 10 sonidos de ejemplo.")
        if st.button("Recuperar los 10 botones de ejemplo", type="primary"):
            seed_default_buttons(user["id"])
            flash("Botones de ejemplo agregados.")
            st.rerun()
        return

    labels = {b["id"]: f"{i + 1}. {b['emoji'] or '🔊'}  {b['name']}" for i, b in enumerate(buttons)}
    bid = st.selectbox("Botón a editar", list(labels), format_func=labels.get, key="edit_sel")
    b = next(x for x in buttons if x["id"] == bid)
    k = f"e{bid}_{v}"

    c1, c2 = st.columns(2, gap="large")
    with c1:
        name = st.text_input("Nombre", b["name"], max_chars=24, key=f"{k}_name")
        emoji = st.text_input("Emoji", b["emoji"] or "🔊", max_chars=8, key=f"{k}_emoji")
        remove_img = False
        if b["image_b64"]:
            ic, cc = st.columns([1, 4])
            ic.image(base64.b64decode(b["image_b64"]), width=64)
            remove_img = cc.checkbox("Quitar imagen y usar el emoji", key=f"{k}_rmimg")
        new_img = st.file_uploader("Cambiar imagen", type=["png", "jpg", "jpeg", "webp", "gif"], key=f"{k}_img")
        color = color_selector(k, b["color"])
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
        start, end = st.slider("✂️ Recorte: inicio y fin (segundos)", 0.0, dur, default, step=0.01, key=trim_key)
        volume = st.slider("Volumen del botón", 0.0, 2.0, float(b["volume"]), 0.05, key=f"{k}_vol")

    if new_img is not None:
        img_b64, img_mime = base64.b64encode(new_img.getvalue()).decode(), new_img.type
    elif remove_img:
        img_b64, img_mime = None, None
    else:
        img_b64, img_mime = b["image_b64"], b["image_mime"]

    st.markdown("#### Vista previa")
    render_preview({"id": bid, "name": name or b["name"], "emoji": emoji, "color": color, "image_b64": img_b64,
                    "image_mime": img_mime, "start_s": start, "end_s": end, "volume": volume}, audio_b64)

    a1, a2, a3, _, a4 = st.columns([1.3, 1, 1, 0.4, 2.2])
    if a1.button("💾 Guardar cambios", type="primary", width="stretch"):
        fields = dict(name=name.strip() or b["name"], emoji=emoji.strip() or "🔊", color=color,
                      start_s=float(start), end_s=None if end >= dur - 0.01 else float(end),
                      volume=float(volume))
        if new_img is not None:
            fields["image_b64"], fields["image_mime"] = read_image(new_img)
        elif remove_img:
            fields["image_b64"], fields["image_mime"] = None, None
        old_path = b["sound_path"]
        if new_snd is not None:
            fields["sound_path"] = save_uploaded_sound(user["username"], new_snd)
        update_button(user["id"], bid, **fields)
        if new_snd is not None:
            cleanup_sound(old_path)
        st.session_state.edit_ver = v + 1
        flash("Cambios guardados.")
        st.rerun()
    if a2.button("⬆ Subir", width="stretch"):
        move_button(user["id"], bid, -1)
        st.rerun()
    if a3.button("⬇ Bajar", width="stretch"):
        move_button(user["id"], bid, +1)
        st.rerun()
    with a4:
        confirm = st.checkbox("Confirmo que quiero eliminarlo", key=f"{k}_confirm")
        if st.button("🗑 Eliminar botón", disabled=not confirm, width="stretch"):
            delete_button(user["id"], bid)
            st.session_state.pop("edit_sel", None)
            st.session_state.edit_ver = v + 1
            flash(f"Botón «{b['name']}» eliminado.", "🗑️")
            st.rerun()

    st.divider()
    if st.button("Agregar otra vez los 10 botones de ejemplo"):
        seed_default_buttons(user["id"])
        flash("Botones de ejemplo agregados al final de tu set.")
        st.rerun()


# ================================================================== MAIN
def main() -> None:
    ensure_default_sounds()
    init_db()
    st.markdown(APP_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("stop_nonce", 0)

    user = st.session_state.get("user")
    if not user:
        show_flash()
        page_auth()
        return

    buttons = get_buttons(user["id"])
    page = sidebar(user, len(buttons))
    show_flash()

    if page.startswith("🎛️"):
        page_pad(buttons)
    elif page.startswith("➕"):
        page_add(user)
    else:
        page_edit(user, buttons)


main()
