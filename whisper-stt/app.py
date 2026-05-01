"""
=============================================================================
Tecno Táctil — Whisper STT Microservicio  v2.0
=============================================================================
API REST asíncrona con Redis Queue (RQ) para manejar colas de transcripción.
Modelo: large-v3 (máxima calidad).

FLUJO ASÍNCRONO:
  1. POST /transcribe         → devuelve job_id inmediatamente (202)
  2. Worker RQ procesa en background
  3. GET  /job/<job_id>       → polling hasta status = "finished"

Endpoints:
  GET  /health              → Estado, modelo y cola
  POST /transcribe          → Encola (multipart/form-data)
  POST /transcribe/url      → Encola desde URL pública
  GET  /job/<job_id>        → Estado y resultado del job
  GET  /queue/stats         → Estadísticas de la cola
  GET  /models              → Modelos disponibles

Auth: Header 'X-API-Key' (excepto /health)
=============================================================================
"""

import os
import logging
import tempfile
import time
import uuid
import urllib.request
from functools import wraps
from pathlib import Path

import whisper
import redis
from rq import Queue, Worker
from rq.job import Job, JobStatus
from rq.exceptions import NoSuchJobError
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whisper-stt")

# ---------------------------------------------------------------------------
# Config desde variables de entorno
# ---------------------------------------------------------------------------
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_LANGUAGE   = os.getenv("WHISPER_LANGUAGE", "es")
WHISPER_API_KEY    = os.getenv("WHISPER_API_KEY", "changeme")
MAX_FILE_SIZE_MB   = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
REDIS_HOST         = os.getenv("REDIS_HOST", "redis")
REDIS_PORT         = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD     = os.getenv("REDIS_PASSWORD", "")
REDIS_DB_QUEUE     = int(os.getenv("REDIS_DB_QUEUE", "2"))
QUEUE_NAME         = "whisper"
JOB_TTL            = 3600
JOB_TIMEOUT        = 600
MAX_QUEUE_SIZE     = int(os.getenv("MAX_QUEUE_SIZE", "50"))

ALLOWED_EXTENSIONS = {"mp3", "mp4", "wav", "ogg", "m4a", "webm", "flac", "oga", "opus"}

# ---------------------------------------------------------------------------
# Conexión Redis + Cola RQ
# ---------------------------------------------------------------------------
redis_conn = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
    db=REDIS_DB_QUEUE,
    decode_responses=False,
)
transcription_queue = Queue(
    QUEUE_NAME,
    connection=redis_conn,
    default_timeout=JOB_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Singleton del modelo Whisper — cargado UNA vez por proceso worker
# ---------------------------------------------------------------------------
_model = None

def get_model():
    """Carga el modelo Whisper la primera vez y lo reutiliza."""
    global _model
    if _model is None:
        logger.info(f"Cargando modelo Whisper '{WHISPER_MODEL_NAME}'...")
        t = time.time()
        _model = whisper.load_model(WHISPER_MODEL_NAME)
        logger.info(f"Modelo cargado en {round(time.time()-t, 2)}s")
    return _model


# ---------------------------------------------------------------------------
# Función de transcripción ejecutada por el Worker RQ (proceso separado)
# ---------------------------------------------------------------------------
def transcribe_job(audio_path: str, language: str, cleanup: bool = True) -> dict:
    """
    Job function para RQ Worker.
    
    Args:
        audio_path: Ruta al archivo de audio temporal.
        language:   Código ISO 639-1 o 'auto'.
        cleanup:    Elimina el archivo temporal al terminar.
    
    Returns:
        dict con text, language_detected, transcription_time, segments.
    """
    model = get_model()
    options = {}
    if language and language.lower() != "auto":
        options["language"] = language

    logger.info(f"[Worker] Transcribiendo: {audio_path} | idioma: {language}")
    t_start = time.time()
    result  = model.transcribe(audio_path, **options)
    elapsed = round(time.time() - t_start, 2)
    logger.info(f"[Worker] Completado en {elapsed}s | idioma: {result.get('language')}")

    segments = [
        {
            "id":    seg["id"],
            "start": round(seg["start"], 2),
            "end":   round(seg["end"], 2),
            "text":  seg["text"].strip(),
        }
        for seg in result.get("segments", [])
    ]

    if cleanup:
        try:
            os.unlink(audio_path)
        except OSError:
            pass

    return {
        "text":               result["text"].strip(),
        "language_detected":  result.get("language"),
        "transcription_time": elapsed,
        "segments":           segments,
    }


# ---------------------------------------------------------------------------
# Flask App — solo maneja HTTP, nunca transcribe directamente
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_MB * 1024 * 1024


def require_api_key(f):
    """Decorador: valida header X-API-Key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if not key or key != WHISPER_API_KEY:
            logger.warning(f"Acceso denegado — IP: {request.remote_addr}")
            return jsonify({"error": "Unauthorized", "message": "X-API-Key inválida o ausente"}), 401
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def queue_is_full() -> bool:
    return len(transcription_queue) >= MAX_QUEUE_SIZE


def job_to_response(job: Job) -> dict:
    """Convierte un objeto Job RQ al formato de respuesta de la API."""
    status = job.get_status()
    status_str = status.value if hasattr(status, "value") else str(status)

    resp = {
        "job_id":      job.id,
        "status":      status_str,
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at":  job.started_at.isoformat()  if job.started_at  else None,
        "ended_at":    job.ended_at.isoformat()     if job.ended_at    else None,
    }

    if status == JobStatus.FINISHED:
        resp["result"]  = job.result
        resp["success"] = True

    elif status == JobStatus.FAILED:
        resp["success"] = False
        resp["error"]   = str(job.exc_info) if job.exc_info else "Error desconocido"

    elif status in (JobStatus.QUEUED, JobStatus.DEFERRED):
        resp["queue_position"] = transcription_queue.get_job_position(job.id)

    return resp


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Estado del servicio — sin autenticación (usado por Docker healthcheck)."""
    try:
        redis_conn.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    workers = Worker.all(queue=transcription_queue, connection=redis_conn)

    return jsonify({
        "status":           "ok" if redis_ok else "degraded",
        "service":          "whisper-stt",
        "model":            WHISPER_MODEL_NAME,
        "language_default": WHISPER_LANGUAGE,
        "redis":            "connected" if redis_ok else "error",
        "queue": {
            "name":           QUEUE_NAME,
            "pending_jobs":   len(transcription_queue),
            "max_size":       MAX_QUEUE_SIZE,
            "workers_active": len(workers),
        },
    }), 200 if redis_ok else 503


@app.route("/queue/stats", methods=["GET"])
@require_api_key
def queue_stats():
    """Estadísticas detalladas de la cola RQ."""
    workers = Worker.all(queue=transcription_queue, connection=redis_conn)
    return jsonify({
        "queue_name":            QUEUE_NAME,
        "pending":               len(transcription_queue),
        "workers_active":        len(workers),
        "failed_jobs":           len(transcription_queue.failed_job_registry),
        "finished_jobs":         len(transcription_queue.finished_job_registry),
        "max_queue_size":        MAX_QUEUE_SIZE,
        "job_ttl_seconds":       JOB_TTL,
        "job_timeout_seconds":   JOB_TIMEOUT,
        "workers_detail": [
            {
                "name":        w.name,
                "state":       w.get_state(),
                "current_job": w.get_current_job_id(),
            }
            for w in workers
        ],
    }), 200


@app.route("/job/<job_id>", methods=["GET"])
@require_api_key
def get_job(job_id: str):
    """
    Consulta estado y resultado de un job.

    Estados:
      queued    → En cola esperando worker
      started   → Worker procesando
      finished  → Resultado disponible en result.text
      failed    → Error, ver campo error
    
    Uso en n8n: polling con Wait node cada 15-20s hasta status == "finished".
    """
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        return jsonify(job_to_response(job)), 200
    except NoSuchJobError:
        return jsonify({
            "error":   "Not Found",
            "message": f"Job '{job_id}' no existe o expiró (TTL: {JOB_TTL}s)",
        }), 404


@app.route("/transcribe", methods=["POST"])
@require_api_key
def transcribe():
    """
    Encola transcripción de archivo de audio (multipart/form-data).

    Form fields:
      file      (required) — archivo de audio
      language  (optional) — ISO 639-1 o 'auto' (default: WHISPER_LANGUAGE env)

    Respuesta 202:
      { "job_id": "...", "status": "queued", "poll_url": "/job/..." }

    Flujo n8n:
      1. POST /transcribe                → guardar job_id
      2. Wait node 20s
      3. GET /job/<job_id>               → verificar status
      4. IF status != finished → volver a 2
      5. Usar {{ $json.result.text }}
    """
    if queue_is_full():
        return jsonify({
            "error":   "Service Unavailable",
            "message": f"Cola llena ({MAX_QUEUE_SIZE} jobs). Reintentar en unos segundos.",
        }), 503

    if "file" not in request.files:
        return jsonify({"error": "Bad Request", "message": "Campo 'file' requerido"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Bad Request", "message": "Nombre de archivo vacío"}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "error":   "Unsupported Media Type",
            "message": f"Extensiones soportadas: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        }), 415

    language = request.form.get("language", WHISPER_LANGUAGE)
    suffix   = Path(secure_filename(file.filename)).suffix or ".ogg"

    # Archivo temporal persistente (el worker lo lee en background)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp")
    file.save(tmp.name)
    tmp.close()

    job = transcription_queue.enqueue(
        transcribe_job,
        tmp.name,
        language,
        True,
        job_id=str(uuid.uuid4()),
        result_ttl=JOB_TTL,
        failure_ttl=JOB_TTL,
    )

    logger.info(f"Job encolado: {job.id} | archivo: {tmp.name} | idioma: {language}")

    return jsonify({
        "job_id":         job.id,
        "status":         "queued",
        "queue_position": transcription_queue.get_job_position(job.id),
        "poll_url":       f"/job/{job.id}",
        "message":        "Job encolado correctamente.",
    }), 202


@app.route("/transcribe/url", methods=["POST"])
@require_api_key
def transcribe_url():
    """
    Descarga audio desde URL y encola transcripción.

    JSON body:
      { "url": "https://...", "language": "es" }

    Respuesta igual a POST /transcribe (202 con job_id).
    """
    if queue_is_full():
        return jsonify({
            "error":   "Service Unavailable",
            "message": f"Cola llena ({MAX_QUEUE_SIZE} jobs). Reintentar en unos segundos.",
        }), 503

    data = request.get_json(force=True, silent=True)
    if not data or "url" not in data:
        return jsonify({"error": "Bad Request", "message": "Campo 'url' requerido"}), 400

    audio_url = data["url"]
    language  = data.get("language", WHISPER_LANGUAGE)
    suffix    = Path(audio_url.split("?")[0]).suffix or ".ogg"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp")
    tmp.close()

    try:
        logger.info(f"Descargando: {audio_url}")
        urllib.request.urlretrieve(audio_url, tmp.name)  # noqa: S310
    except Exception as e:
        os.unlink(tmp.name)
        return jsonify({"error": "Bad Gateway", "message": f"No se pudo descargar el audio: {e}"}), 502

    job = transcription_queue.enqueue(
        transcribe_job,
        tmp.name,
        language,
        True,
        job_id=str(uuid.uuid4()),
        result_ttl=JOB_TTL,
        failure_ttl=JOB_TTL,
    )

    logger.info(f"Job encolado (URL): {job.id} | fuente: {audio_url}")

    return jsonify({
        "job_id":         job.id,
        "status":         "queued",
        "queue_position": transcription_queue.get_job_position(job.id),
        "poll_url":       f"/job/{job.id}",
        "source_url":     audio_url,
    }), 202


@app.route("/models", methods=["GET"])
@require_api_key
def list_models():
    return jsonify({
        "current_model": WHISPER_MODEL_NAME,
        "available": {
            "tiny":     {"params": "39M",   "ram": "~1GB",  "speed": "~32x", "notes": "Muy rápido, calidad básica"},
            "base":     {"params": "74M",   "ram": "~1GB",  "speed": "~16x", "notes": "Rápido, calidad aceptable"},
            "small":    {"params": "244M",  "ram": "~2GB",  "speed": "~6x",  "notes": "Buen balance"},
            "medium":   {"params": "769M",  "ram": "~5GB",  "speed": "~2x",  "notes": "Recomendado para español"},
            "large-v2": {"params": "1550M", "ram": "~10GB", "speed": "1x",   "notes": "Alta precisión"},
            "large-v3": {"params": "1550M", "ram": "~10GB", "speed": "1x",   "notes": "ACTIVO — Máxima calidad"},
        },
    }), 200


# ---------------------------------------------------------------------------
# Errores globales
# ---------------------------------------------------------------------------
@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "Payload Too Large", "message": f"Máximo {MAX_FILE_SIZE_MB}MB"}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not Found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method Not Allowed"}), 405


# ---------------------------------------------------------------------------
# Inicio
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info(" Tecno Táctil — Whisper STT v2.0")
    logger.info(f" Modelo:    {WHISPER_MODEL_NAME}")
    logger.info(f" Cola:      {QUEUE_NAME} @ Redis DB{REDIS_DB_QUEUE}")
    logger.info(f" Max jobs:  {MAX_QUEUE_SIZE}")
    logger.info(f" Puerto:    9000")
    logger.info("=" * 60)
    app.run(host="0.0.0.0", port=9000, debug=False, threaded=True)
