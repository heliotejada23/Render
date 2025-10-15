import os
import json
import re
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import dateparser
from dateparser.search import search_dates

# -------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------
TELEGRAM_API = os.getenv("TELEGRAM_API")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

# Google creds (desde env o token.json)
GOOGLE_TOKEN = os.getenv("GOOGLE_TOKEN_JSON")
if GOOGLE_TOKEN:
    creds = Credentials.from_authorized_user_info(json.loads(GOOGLE_TOKEN))
else:
    with open("token.json", "r") as f:
        creds = Credentials.from_authorized_user_info(json.load(f))

calendar_service = build("calendar", "v3", credentials=creds)

# Fallback TZ si no podemos leerla de Google
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Madrid")

app = FastAPI()

# -------------------------------------------------------------
# TELEGRAM
# -------------------------------------------------------------
def send_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_API}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def download_file(file_id: str) -> bytes:
    file_info_url = f"https://api.telegram.org/bot{TELEGRAM_API}/getFile?file_id={file_id}"
    file_info = requests.get(file_info_url).json()
    if not file_info.get("ok") or "result" not in file_info:
        raise Exception(f"Error obteniendo archivo de Telegram: {file_info}")
    file_path = file_info["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_API}/{file_path}"
    resp = requests.get(file_url)
    if resp.status_code != 200:
        raise Exception(f"Error al descargar audio: {resp.status_code}")
    return resp.content

# -------------------------------------------------------------
# WHISPER (Hugging Face)
# -------------------------------------------------------------
def transcribe_audio(audio_data: bytes) -> str:
    resp = requests.post(
        HF_MODEL_URL,
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "audio/ogg",
        },
        data=audio_data,
        timeout=120,
    )
    if resp.status_code != 200:
        raise Exception(f"Error desde Hugging Face: {resp.text}")
    result = resp.json()
    text = result.get("text")
    if not text and isinstance(result, list) and result and "text" in result[0]:
        text = result[0]["text"]
    return (text or "").strip()

# -------------------------------------------------------------
# ZONA HORARIA
# -------------------------------------------------------------
def get_user_timezone() -> str:
    """Lee la timezone de tu cuenta de Google Calendar; usa DEFAULT_TZ si falla."""
    try:
        settings = calendar_service.settings().get(setting='timezone').execute()
        tz = settings.get("value")
        if tz:
            return tz
    except Exception as e:
        print(f"⚠️ No se pudo leer timezone de Google: {e}")
    return DEFAULT_TZ

# -------------------------------------------------------------
# NLP de fecha/hora + limpieza de título
# -------------------------------------------------------------
SPANISH_FIXES = {
    "manana": "mañana",
    "pasado manana": "pasado mañana",
    "miercoles": "miércoles",
    "sabado": "sábado",
}

TIME_WORDS = [
    r"\b(hoy|mañana|pasado mañana)\b",
    r"\b(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b",
    r"\b(a las|sobre las|hacia las|a la)\b\s*\d{1,2}([:.]\d{1,2})?\s*(am|pm|h|hs)?",
    r"\b(\d{1,2}[:.]\d{2})\b\s*(am|pm|h|hs)?",
    r"\b(\d{1,2})\s*(am|pm)\b",
    r"\b(mañana|tarde|noche|mediodía|mediodia)\b",
    r"\b(este|esta|el)\s+(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b",
    r"\b(\d{1,2}\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre))\b",
]

TIME_PATTERN = re.compile("|".join(TIME_WORDS), re.IGNORECASE)

def normalize_spanish(text: str) -> str:
    t = text.lower()
    for wrong, correct in SPANISH_FIXES.items():
        t = t.replace(wrong, correct)
    # normalizar “mediodía”
    t = t.replace("mediodia", "mediodía")
    return t

def extract_datetime_and_clean(text: str, tz: str):
    """
    Devuelve (dt, has_time, clean_title)
    - dt: datetime con TZ si encuentra algo
    - has_time: True si la expresión incluía hora
    - clean_title: título sin la parte de fecha/hora
    """
    original = text.strip()
    t = normalize_spanish(original)

    # 1) busca todas las fechas/horas en el texto
    results = search_dates(
        t,
        languages=["es"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )

    chosen_dt = None
    chosen_span = None
    has_time = False

    if results:
        # results = [(matched_substring, datetime_obj), ...]
        # preferimos la primera que contenga hora explícita
        for match_text, dt in results:
            # ¿la subcadena trae hora?
            if re.search(r"\d{1,2}([:.]\d{1,2})?\s*(am|pm|h|hs)?", match_text):
                chosen_dt = dt
                chosen_span = match_text
                has_time = True
                break
        # si ninguna traía hora, cogemos la primera (será fecha sola)
        if chosen_dt is None:
            chosen_dt = results[0][1]
            chosen_span = results[0][0]
            has_time = False

    # 2) Si no encontró nada, heurísticas básicas
    if chosen_dt is None:
        now = datetime.now(ZoneInfo(tz))
        if "pasado mañana" in t:
            chosen_dt = now + timedelta(days=2)
            chosen_span = "pasado mañana"
        elif "mañana" in t:
            chosen_dt = now + timedelta(days=1)
            chosen_span = "mañana"
        elif "hoy" in t:
            chosen_dt = now
            chosen_span = "hoy"
        # has_time sigue False aquí

    # 3) Si detectamos “mediodía/tarde/noche” sin hora, ajustamos
    if chosen_dt is not None and not has_time:
        if "mediodía" in t:
            chosen_dt = chosen_dt.replace(hour=12, minute=0, second=0, microsecond=0)
            has_time = True
        elif "tarde" in t:
            chosen_dt = chosen_dt.replace(hour=16, minute=0, second=0, microsecond=0)
            has_time = True
        elif "noche" in t:
            chosen_dt = chosen_dt.replace(hour=20, minute=0, second=0, microsecond=0)
            has_time = True

    # 4) limpieza del título: elimina la porción de fecha/hora detectada
    clean_title = original
    if chosen_span:
        # quitar solo esa porción (case-insensitive, espacios sobrantes)
        clean_title = re.sub(re.escape(chosen_span), "", clean_title, flags=re.IGNORECASE)
        # quitar conectores típicos cercanos a la hora/fecha
        clean_title = re.sub(r"\b(a las|sobre las|hacia las|el|este|esta|para|de)\b", "", clean_title, flags=re.IGNORECASE)
        # colapsar espacios
        clean_title = re.sub(r"\s{2,}", " ", clean_title).strip(" :,-")

    # 5) si al limpiar quedó vacío, usa un fallback
    if not clean_title:
        clean_title = "Tarea" if not has_time else "Evento"

    # 6) asegurar TZ awareness
    if chosen_dt is not None and chosen_dt.tzinfo is None:
        chosen_dt = chosen_dt.replace(tzinfo=ZoneInfo(tz))

    print(f"🧪 extract_datetime_and_clean → dt={chosen_dt}, has_time={has_time}, title='{clean_title}'")
    return chosen_dt, has_time, clean_title

def classify_intent(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["tarea", "recordatorio", "pendiente", "hacer", "recordarme", "recuerdame", "recuérdame"]):
        return "task"
    if any(w in t for w in ["reunión", "cita", "evento", "llamada", "videollamada", "quedar"]):
        return "event"
    # por defecto: si hay hora → evento; si no → tarea
    return "event"

# -------------------------------------------------------------
# CALENDAR
# -------------------------------------------------------------
def create_calendar_event(summary: str, start_dt: datetime, tz: str, duration_minutes: int = 60) -> str | None:
    try:
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        body = {
            "summary": summary.strip().capitalize(),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
        }
        created = calendar_service.events().insert(calendarId="primary", body=body).execute()
        print(f"✅ Evento creado: {created.get('htmlLink')}")
        return created.get("htmlLink")
    except Exception as e:
        print(f"⚠️ Error al crear evento: {e}")
        return None

def create_calendar_task(summary: str, due_dt: datetime, tz: str) -> str | None:
    """
    Si due_dt trae hora → bloque con hora exacta.
    Si no trae hora → evento de día completo.
    """
    try:
        if due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0:
            body = {
                "summary": f"Tarea: {summary.strip().capitalize()}",
                "start": {"date": due_dt.date().isoformat()},
                "end": {"date": (due_dt.date() + timedelta(days=1)).isoformat()},
            }
        else:
            end_dt = due_dt + timedelta(minutes=60)
            body = {
                "summary": f"Tarea: {summary.strip().capitalize()}",
                "start": {"dateTime": due_dt.isoformat(), "timeZone": tz},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
            }
        created = calendar_service.events().insert(calendarId="primary", body=body).execute()
        print(f"✅ Tarea creada: {created.get('htmlLink')}")
        return created.get("htmlLink")
    except Exception as e:
        print(f"⚠️ Error al crear tarea: {e}")
        return None

# -------------------------------------------------------------
# TELEGRAM: MODELO
# -------------------------------------------------------------
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict

# -------------------------------------------------------------
# WEBHOOK
# -------------------------------------------------------------
@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    message = update.message
    chat_id = message["chat"]["id"]
    print(f"📩 Mensaje recibido: {message}")

    tz = get_user_timezone()
    print(f"🕒 Timezone detectada: {tz}")

    # --- Texto ---
    if "text" in message:
        text = message["text"].strip()
        intent = classify_intent(text)
        dt, has_time, clean_title = extract_datetime_and_clean(text, tz)

        if dt:
            if intent == "event":
                link = create_calendar_event(clean_title, dt, tz)
                send_message(chat_id, f"📅 Evento creado ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear el evento.")
            else:
                link = create_calendar_task(clean_title, dt, tz)
                send_message(chat_id, f"✅ Tarea creada ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear la tarea.")
        else:
            send_message(chat_id, "⚠️ No encontré fecha u hora. Dime algo como: 'tarea mañana a las 8: pagar luz'.")

        return {"ok": True}

    # --- Voz ---
    if "voice" in message:
        try:
            send_message(chat_id, "🎧 Procesando tu nota de voz...")
            file_id = message["voice"]["file_id"]
            audio_data = download_file(file_id)
            text = transcribe_audio(audio_data)
            if not text:
                send_message(chat_id, "⚠️ No pude transcribir el audio.")
                return {"ok": True}

            intent = classify_intent(text)
            dt, has_time, clean_title = extract_datetime_and_clean(text, tz)

            if dt:
                if intent == "event":
                    link = create_calendar_event(clean_title, dt, tz)
                    send_message(chat_id, f"📅 Evento creado ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear el evento.")
                else:
                    link = create_calendar_task(clean_title, dt, tz)
                    send_message(chat_id, f"✅ Tarea creada ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear la tarea.")
            else:
                send_message(chat_id, f"🗣️ Entendí: {text}\n⚠️ No encontré fecha u hora. Di: 'mañana a las 8 ...'")

        except Exception as e:
            print(f"❌ Error procesando audio: {e}")
            send_message(chat_id, f"❌ Error: {e}")

    return {"ok": True}

# -------------------------------------------------------------
# SALUD
# -------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "🤖 Bot Telegram + Whisper + Calendar con fechas/horas exactas y título limpio"}
