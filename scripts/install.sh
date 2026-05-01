#!/bin/bash
# =============================================================================
# Tecno Táctil — Script de instalación del ecosistema
# Ubuntu 24.04 | n8n + Evolution API + PostgreSQL + Redis + Whisper STT
# =============================================================================
# USO: bash install.sh
# Ejecutar como root o usuario con sudo

set -euo pipefail

# --- Colores para output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
info() { echo -e "${BLUE}[→]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo -e "${BLUE}============================================================"
echo " Tecno Táctil — Instalación del Ecosistema"
echo "============================================================${NC}"
echo ""

# =============================================================================
# PASO 1: Actualizar sistema e instalar dependencias base
# =============================================================================
info "PASO 1: Actualizando sistema..."
apt-get update -qq && apt-get upgrade -y -qq
apt-get install -y -qq \
    curl wget git vim htop \
    certbot \
    ufw \
    fail2ban \
    ca-certificates \
    gnupg \
    lsb-release \
    apt-transport-https
log "Sistema actualizado"

# =============================================================================
# PASO 2: Instalar Docker y Docker Compose
# =============================================================================
info "PASO 2: Instalando Docker..."

if command -v docker &> /dev/null; then
    warn "Docker ya está instalado: $(docker --version)"
else
    # Agregar repositorio oficial de Docker
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -qq
    apt-get install -y -qq \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

    # Iniciar y habilitar Docker
    systemctl enable --now docker
    log "Docker instalado: $(docker --version)"
fi

# =============================================================================
# PASO 3: Configurar Firewall (UFW)
# =============================================================================
info "PASO 3: Configurando firewall UFW..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
log "Firewall configurado (SSH + HTTP + HTTPS)"

# =============================================================================
# PASO 4: Crear directorio del proyecto
# =============================================================================
info "PASO 4: Creando estructura de directorios..."
PROJECT_DIR="/opt/tecnotactil"
mkdir -p $PROJECT_DIR/{nginx/conf.d,whisper-stt,scripts}
mkdir -p /var/www/certbot
log "Directorios creados en $PROJECT_DIR"

# =============================================================================
# PASO 5: Generar certificados SSL con Certbot
# =============================================================================
info "PASO 5: Obteniendo certificados SSL..."
warn "Asegúrate de que los dominios apunten a esta IP antes de continuar"
echo ""
echo "  bak.tecnotactil.com       → $(curl -s ifconfig.me)"
echo "  evolution.tecnotactil.com → $(curl -s ifconfig.me)"
echo ""
read -p "¿Los DNS ya están propagados? (s/n): " dns_ready

if [[ "$dns_ready" == "s" || "$dns_ready" == "S" ]]; then
    # Parar Nginx si está corriendo en el host
    systemctl stop nginx 2>/dev/null || true

    certbot certonly --standalone \
        -d bak.tecnotactil.com \
        --non-interactive \
        --agree-tos \
        --email admin@tecnotactil.com \
        --no-eff-email

    certbot certonly --standalone \
        -d evolution.tecnotactil.com \
        --non-interactive \
        --agree-tos \
        --email admin@tecnotactil.com \
        --no-eff-email

    log "Certificados SSL obtenidos"
else
    warn "Saltando SSL por ahora. Ejecuta certbot manualmente después."
    warn "Comando: certbot certonly --standalone -d bak.tecnotactil.com"
fi

# =============================================================================
# PASO 6: Configurar renovación automática de certificados
# =============================================================================
info "PASO 6: Configurando renovación automática de SSL..."
cat > /etc/cron.d/certbot-renew << 'EOF'
# Renovar certificados SSL cada 12 horas
0 */12 * * * root certbot renew --quiet --deploy-hook "docker exec tt_nginx nginx -s reload"
EOF
log "Cron de renovación SSL configurado"

# =============================================================================
# PASO 7: Copiar archivos del proyecto
# =============================================================================
info "PASO 7: Copiando archivos del proyecto..."
warn "Copia los archivos del proyecto a: $PROJECT_DIR"
warn "Estructura requerida:"
echo ""
echo "  $PROJECT_DIR/"
echo "  ├── docker-compose.yml"
echo "  ├── .env                    ← Editar con tus credenciales"
echo "  ├── nginx/conf.d/"
echo "  │   ├── n8n.conf"
echo "  │   └── evolution.conf"
echo "  ├── whisper-stt/"
echo "  │   ├── Dockerfile"
echo "  │   ├── requirements.txt"
echo "  │   └── app.py"
echo "  └── scripts/"
echo "      └── init-db.sh"
echo ""

# =============================================================================
# PASO 8: Generar credenciales seguras
# =============================================================================
info "PASO 8: Generando credenciales seguras..."
echo ""
echo -e "${YELLOW}Guarda estas credenciales en tu .env:${NC}"
echo ""
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 32)"
echo "REDIS_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 32)"
echo "N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)"
echo "EVOLUTION_API_KEY=$(openssl rand -hex 32)"
echo "WHISPER_API_KEY=$(openssl rand -hex 16)"
echo ""

# =============================================================================
# PASO 9: Permisos del script de init de BD
# =============================================================================
info "PASO 9: Aplicando permisos..."
chmod +x $PROJECT_DIR/scripts/init-db.sh 2>/dev/null || warn "Aplica chmod +x scripts/init-db.sh manualmente"

echo ""
log "Instalación base completada"
echo ""
echo -e "${BLUE}============================================================"
echo " PRÓXIMOS PASOS MANUALES:"
echo "============================================================${NC}"
echo ""
echo "  1. Editar $PROJECT_DIR/.env con tus credenciales"
echo "  2. cd $PROJECT_DIR"
echo "  3. docker compose up -d postgres redis"
echo "     (esperar ~20s a que PostgreSQL inicie)"
echo "  4. docker compose up -d --build"
echo "  5. docker compose logs -f"
echo ""
echo -e "${GREEN}  URLs finales:${NC}"
echo "    n8n:       https://bak.tecnotactil.com"
echo "    Evolution: https://evolution.tecnotactil.com"
echo "    Whisper:   http://tt_whisper:9000 (solo interno)"
echo ""
