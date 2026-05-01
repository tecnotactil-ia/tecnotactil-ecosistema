
# 🤖 Tecno Táctil — Ecosistema de Automatización Inteligente

<p align="center">
  <img src="https://img.shields.io/badge/n8n-latest-brightgreen" alt="n8n">
  <img src="https://img.shields.io/badge/Evolution%20API-latest-blue" alt="Evolution API">
  <img src="https://img.shields.io/badge/Whisper%20STT-v2.0-orange" alt="Whisper STT">
  <img src="https://img.shields.io/badge/PostgreSQL-16-blue" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis-7-red" alt="Redis">
  <img src="https://img.shields.io/badge/Nginx-Alpine-green" alt="Nginx">
  <img src="https://img.shields.io/badge/Docker-Production%20Ready-2496ED?logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/Platform-Ubuntu%2024.04-E95420?logo=ubuntu" alt="Ubuntu">
</p>

**Stack de automatización inteligente todo en uno**: n8n como motor de workflows, Evolution API como gateway WhatsApp, y Whisper STT como motor de transcripción de audio a texto. Todo orquestado con Docker, listo para producción.

---

## 📖 ¿Qué es Tecno Táctil?

Es un ecosistema Dockerizado que integra:

- **n8n** — Automatizaciones potentes con +400 integraciones nativas
- **Evolution API** — Gateway WhatsApp no oficial con soporte multi-instancia
- **Whisper STT** — Transcripción de voz a texto (OpenAI Whisper) con cola asíncrona
- **PostgreSQL 16** — Base de datos compartida por todos los servicios
- **Redis 7** — Caché y colas de trabajo
- **Nginx** — Proxy inverso con SSL (Let's Encrypt)

### 🎯 Caso de uso principal: Bot de WhatsApp con IA

```
Cliente WhatsApp → Evolution API → n8n → Whisper STT (transcripción)
                                    ↓
                               DeepSeek / OpenAI (IA)
                                    ↓
                            Respuesta automática al cliente
```

---

## 🏗️ Arquitectura

```
Internet
   │
   ├── bak.tecnotactil.com (443)       → Nginx → n8n:5678
   └── evolution.tecnotactil.com (443) → Nginx → Evolution API:8080

Red interna Docker (172.30.0.0/16)
   ├── tt_postgres   (PostgreSQL 16)
   │   ├── tt_n8n        ← Base de datos de n8n
   │   └── tt_evolution  ← Base de datos de Evolution API
   ├── tt_redis      (Redis 7, caché + colas compartidas)
   ├── tt_n8n        (n8n latest, modo queue)
   ├── tt_evolution  (Evolution API latest)
   ├── tt_whisper    (Whisper STT API, Flask + RQ)
   └── tt_whisper_worker (Procesamiento asíncrono de transcripciones)
```

---

## 🚀 Instalación paso a paso

### Prerrequisitos
- Ubuntu 24.04 LTS
- Mínimo: 4 vCPU / 8 GB RAM (12 GB recomendados para modelo Whisper `large-v3`)
- Dominios DNS apuntando a la IP del VPS con TTL bajo:
  - `bak.tecnotactil.com`
  - `evolution.tecnotactil.com`

### 1 — Preparar el servidor

```bash
# Subir todos los archivos al VPS
scp -r tecnotactil/ root@TU_IP:/opt/tecnotactil

# Conectarse al VPS
ssh root@TU_IP

cd /opt/tecnotactil
chmod +x scripts/install.sh scripts/init-db.sh
```

### 2 — Ejecutar el instalador base

```bash
bash scripts/install.sh
```

Esto instala Docker, configura UFW y obtiene los certificados SSL (si los DNS ya propagaron).

### 3 — Configurar las credenciales

```bash
cp .env .env.backup
nano .env
```

Editar **TODAS** las variables marcadas como `CAMBIAR_POR_...`. Usar las credenciales generadas por el instalador.

### 4 — Levantar el ecosistema

```bash
cd /opt/tecnotactil

# Primero levantar la base de datos y esperar que inicie
docker compose up -d postgres redis
sleep 25

# Construir Whisper y levantar todo
docker compose up -d --build

# Verificar estado
docker compose ps
docker compose logs -f
```

### 5 — Verificar servicios

```bash
# PostgreSQL — verificar las 2 BDs creadas
docker exec tt_postgres psql -U tt_admin -c "\l"

# Redis — verificar conexión
docker exec tt_redis redis-cli -a TU_REDIS_PASSWORD ping

# Whisper — verificar health
curl http://localhost:9000/health
docker exec tt_n8n wget -qO- http://tt_whisper:9000/health
```

### 6 — Acceder a los servicios

| Servicio | URL | Notas |
|---|---|---|
| n8n | https://bak.tecnotactil.com | Crear cuenta en primer acceso |
| Evolution API | https://evolution.tecnotactil.com | Header: `apikey: TU_EVOLUTION_API_KEY` |
| Whisper STT | `http://tt_whisper:9000` | Solo acceso interno (desde n8n) |

---

## 🎙️ Whisper STT API

Microservicio Flask con arquitectura asíncrona basada en Redis Queue (RQ). Soporta streaming de audio, descarga desde URLs y polling de resultados.

### Modelos disponibles

| Modelo | Parámetros | RAM | Velocidad | Calidad |
|---|---|---|---|---|
| `tiny` | 39M | ~1 GB | ~32× | Básica |
| `base` | 74M | ~1 GB | ~16× | Aceptable |
| `small` | 244M | ~2 GB | ~6× | Buena |
| `medium` | 769M | ~5 GB | ~2× | Muy buena (recomendado español) |
| `large-v2` | 1550M | ~10 GB | 1× | Alta precisión |
| **`large-v3`** (default) | **1550M** | **~10 GB** | **1×** | **Máxima calidad** |

### Endpoints

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servicio (sin auth) |
| `POST` | `/transcribe` | Encola archivo de audio (multipart/form-data) |
| `POST` | `/transcribe/url` | Descarga audio desde URL y lo encola |
| `GET` | `/job/:id` | Estado y resultado de un job |
| `GET` | `/queue/stats` | Estadísticas de la cola |
| `GET` | `/models` | Modelos disponibles |

### Usar Whisper STT desde n8n

**Opción 1: Transcribir archivo binario (audio directo)**

Configurar un nodo **HTTP Request** en n8n:

```
Method:   POST
URL:      http://tt_whisper:9000/transcribe
Headers:  X-API-Key: {{ $env.WHISPER_API_KEY }}
Body:     Form-Data
  - file: {{ $binary.data }}       ← Binario del audio
  - language: es                   ← Opcional
```

Respuesta:
```json
{
  "success": true,
  "text": "Hola, necesito información sobre sus servicios...",
  "language_detected": "es",
  "duration_seconds": 2.34,
  "segments": [
    {"id": 0, "start": 0.0, "end": 3.2, "text": "Hola, necesito información..."}
  ]
}
```

**Opción 2: Transcribir desde URL (audio de WhatsApp vía Evolution)**

```json
POST http://tt_whisper:9000/transcribe/url
Headers: X-API-Key: TU_WHISPER_API_KEY
Content-Type: application/json

{
  "url": "https://tu-nextcloud.com/audio123.ogg",
  "language": "es"
}
```

### Flujo típico: WhatsApp → Transcripción → IA → Respuesta

```
[Evolution API Webhook]
   └─ Recibe mensaje de audio de WhatsApp
[n8n IF] — ¿tipo = audio?
   └─ [HTTP Request] → Descargar audio desde mediaUrl
   └─ [HTTP Request] → POST /transcribe (Whisper)
   └─ [DeepSeek/AI] → Procesar texto transcrito
   └─ [Evolution API] → Responder al usuario
```

---

## 🛠️ Comandos útiles

```bash
# Ver logs de un servicio específico
docker compose logs -f n8n
docker compose logs -f evolution
docker compose logs -f whisper
docker compose logs -f postgres

# Reiniciar un servicio
docker compose restart n8n

# Actualizar n8n a la última versión
docker compose pull n8n && docker compose up -d n8n

# Actualizar Evolution API
docker compose pull evolution && docker compose up -d evolution

# Backup de PostgreSQL
docker exec tt_postgres pg_dumpall -U tt_admin > backup_$(date +%Y%m%d).sql

# Ver uso de recursos
docker stats

# Entrar a PostgreSQL
docker exec -it tt_postgres psql -U tt_admin -d tt_n8n

# Limpiar logs de Docker (liberar espacio)
docker system prune -f
```

---

## 🔧 Solución de problemas comunes

### n8n no inicia — error de BD
```bash
# Verificar que PostgreSQL esté healthy
docker compose ps postgres
# Verificar que la BD tt_n8n existe
docker exec tt_postgres psql -U tt_admin -c "\l"
```

### Whisper tarda en el primer arranque
Normal — descarga el modelo Whisper (~1.5 GB para `medium`, ~2.8 GB para `large-v3`) en el primer inicio.
```bash
docker compose logs -f whisper
# Esperar mensaje: "✓ Modelo 'large-v3' cargado en Xs"
```

### Certificado SSL no encontrado
```bash
# Si los DNS ya propagaron, regenerar certificados:
docker compose stop nginx
certbot certonly --standalone -d bak.tecnotactil.com
certbot certonly --standalone -d evolution.tecnotactil.com
docker compose start nginx
```

### Evolution API — error de conexión a BD
```bash
# Verificar que POSTGRES_DB_EVOLUTION existe
docker exec tt_postgres psql -U tt_admin -c "\l" | grep evolution
```

---

## 📁 Estructura del proyecto

```
tecnotactil/
├── docker-compose.yml          # Orquestación de servicios
├── .env                        # Variables de entorno (NO SUBIR)
├── .gitignore                  # Exclusiones de git
├── README.md                   # Esta documentación
├── nginx/
│   └── conf.d/
│       ├── n8n.conf            # Proxy inverso n8n + SSL
│       └── evolution.conf      # Proxy inverso Evolution + SSL
├── whisper-stt/
│   ├── Dockerfile              # Imagen Python 3.11 + Whisper
│   ├── app.py                  # API Flask con cola RQ
│   └── requirements.txt        # Dependencias Python
└── scripts/
    ├── install.sh              # Instalador base del VPS
    └── init-db.sh              # Init multi-BD PostgreSQL
```

---

## 🔒 Seguridad

- SSL/TLS con Let's Encrypt (renovación automática cada 12h)
- Autenticación por API Key en Evolution API y Whisper STT
- Headers de seguridad HSTS, X-Frame-Options, X-Content-Type-Options
- Redes Docker aisladas
- Firewall UFW configurado (solo SSH, HTTP, HTTPS)
- `.env` excluido del repositorio con `.gitignore`

---

## ⚡ Requisitos de hardware

| Componente | Modelo `medium` | Modelo `large-v3` |
|---|---|---|
| CPU | 4 vCPU | 4 vCPU |
| RAM | 8 GB | 12 GB |
| Disco | 20 GB | 30 GB |
| GPU | No requerida | No requerida |

---

## 📄 Licencia

MIT © 2025 Tecno Táctil
