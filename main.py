import os
import json
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import dateparser

import re
from datetime import datetime

# -------------------------------------------------------------
# CONFIGURACIÓN
# -------------------------------------------------------------
TELEGRAM_API = os.getenv("TELEGRAM_API")
HF_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
HF_MODEL_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

# Carga de credenciales de Google
GOOGLE_TOKEN = os.getenv("GOOGLE_TOKEN_JSON")
if GOOGLE_TOKEN:
    creds = Credentials.from_authorized_user_info(json.loads(GOOGLE_TOKEN))
else:
    with open("token.json", "r") as f:
        creds = Credentials.from_authorized_user_info(json.load(f))

calendar_service = build("calendar", "v3", credentials=creds)
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "Europe/Madrid")

app = FastAPI()

# -------------------------------------------------------------
# FUNCIONES AUXILIARES
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
    """Obtiene la zona horaria del usuario desde Google Calendar"""
    try:
        settings = calendar_service.settings().get(setting='timezone').execute()
        tz = settings.get("value")
        if tz:
            return tz
    except Exception as e:
        print(f"⚠️ No se pudo leer timezone de Google: {e}")
    return DEFAULT_TZ

# -------------------------------------------------------------
# DETECCIÓN DE FECHAS Y TAREAS
# -------------------------------------------------------------
def parse_datetime_from_text(text: str, tz: str) -> datetime | None:
    """
    Detecta fecha/hora en español con tolerancia a errores y expresiones comunes.
    """
    original_text = text
    text = text.lower()

    # Correcciones básicas de palabras sin tilde
    replacements = {
        "manana": "mañana",
        "pasado manana": "pasado mañana",
        "miercoles": "miércoles",
        "sabado": "sábado",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    # Intento 1: usar dateparser normalmente
    dt = dateparser.parse(
        text,
        languages=["es"],
        settings={
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if dt:
        print(f"✅ dateparser reconoció fecha/hora: {dt}")
        return dt

    # Intento 2: expresiones comunes tipo "mañana", "pasado mañana"
    now = datetime.now(ZoneInfo(tz))
    if "pasado mañana" in text:
        return now + timedelta(days=2)
    if "mañana" in text:
        return now + timedelta(days=1)
    if "hoy" in text:
        return now

    # Intento 3: día de la semana (“lunes”, “viernes”, etc.)
    weekdays = {
        "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
        "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6
    }
    for day_name, day_num in weekdays.items():
        if day_name in text:
            days_ahead = (day_num - now.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            print(f"✅ Detectado día de la semana: {day_name} → {target}")
            return target

    # Intento 4: hora simple (“a las 9”, “a las 10:30”)
    match = re.search(r"a las (\d{1,2})(?:[:\.](\d{1,2}))?", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < now:
            candidate += timedelta(days=1)
        print(f"✅ Detectada hora sin fecha: {candidate}")
        return candidate

    print(f"⚠️ No se encontró fecha/hora en: {original_text}")
    return None

def classify_intent(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["tarea", "recordatorio", "pendiente", "hacer", "recordarme", "recuerdame", "recuérdame"]):
        return "task"
    if any(w in t for w in ["reunión", "cita", "evento", "llamada", "videollamada", "quedar"]):
        return "event"
    return "event"

def ensure_time(dt: datetime | None, tz: str, default_hour: int = 9, default_minute: int = 0) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        dt = dt.replace(hour=default_hour, minute=default_minute)
    return dt

# -------------------------------------------------------------
# CREACIÓN DE EVENTOS Y TAREAS
# -------------------------------------------------------------
def create_calendar_event(summary: str, start_dt: datetime, tz: str, duration_minutes: int = 30) -> str | None:
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
    try:
        if due_dt.hour == 0 and due_dt.minute == 0 and due_dt.second == 0:
            body = {
                "summary": f"Tarea: {summary.strip().capitalize()}",
                "start": {"date": due_dt.date().isoformat()},
                "end": {"date": (due_dt.date() + timedelta(days=1)).isoformat()},
            }
        else:
            end_dt = due_dt + timedelta(minutes=30)
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
# TELEGRAM
# -------------------------------------------------------------
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict

# -------------------------------------------------------------
# WEBHOOK TELEGRAM
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
        parsed = parse_datetime_from_text(text, tz)
        parsed = ensure_time(parsed, tz, 10 if intent == "event" else 9)

        if parsed:
            if intent == "event":
                link = create_calendar_event(text, parsed, tz)
                send_message(chat_id, f"📅 Evento creado ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear el evento.")
            else:
                link = create_calendar_task(text, parsed, tz)
                send_message(chat_id, f"✅ Tarea creada ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear la tarea.")
        else:
            send_message(chat_id, "⚠️ No encontré fecha u hora. Dime algo como: 'reunión mañana a las 10'.")

    # --- Audio ---
    elif "voice" in message:
        try:
            send_message(chat_id, "🎧 Procesando tu nota de voz...")
            file_id = message["voice"]["file_id"]
            audio_data = download_file(file_id)
            text = transcribe_audio(audio_data)
            if not text:
                send_message(chat_id, "⚠️ No pude transcribir el audio.")
                return {"ok": True}

            send_message(chat_id, f"🗣️ He entendido: {text}")
            intent = classify_intent(text)
            parsed = parse_datetime_from_text(text, tz)
            parsed = ensure_time(parsed, tz, 10 if intent == "event" else 9)

            if parsed:
                if intent == "event":
                    link = create_calendar_event(text, parsed, tz)
                    send_message(chat_id, f"📅 Evento creado ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear el evento.")
                else:
                    link = create_calendar_task(text, parsed, tz)
                    send_message(chat_id, f"✅ Tarea creada ({tz}):\n🔗 {link}" if link else "⚠️ No pude crear la tarea.")
            else:
                send_message(chat_id, "⚠️ No encontré fecha u hora en tu audio.")
        except Exception as e:
            print(f"❌ Error procesando audio: {e}")
            send_message(chat_id, f"❌ Error interno: {e}")

    return {"ok": True}

# -------------------------------------------------------------
# RUTA PRINCIPAL
# -------------------------------------------------------------
@app.get("/")
def root():
    return {"status": "ok", "message": "🤖 Bot Telegram + Whisper + Calendar con zona horaria automática 🚀"}

