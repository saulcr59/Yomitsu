#!/usr/bin/env bash
# Yomitsu — instalador para Ubuntu
#
# Opcion A — SSH:
#   git clone git@github.com:saulcr59/Yomitsu.git /opt/yomitsu
#   sudo bash /opt/yomitsu/install.sh
#
# Opcion B — Token de GitHub (repo privado, sin SSH):
#   sudo bash /opt/yomitsu/install.sh --token <GITHUB_PAT>
#   (o: git clone https://TOKEN@github.com/saulcr59/Yomitsu.git /opt/yomitsu)
set -euo pipefail

REPO_URL="https://github.com/saulcr59/Yomitsu.git"
INSTALL_DIR="/opt/yomitsu"
OLLAMA_MODEL="hf.co/unsloth/Hy-MT2-7B-GGUF:UD-Q4_K_XL"
GH_TOKEN=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info() { echo "  $*"; }
step() { echo; echo "==> $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Ejecutar como root: sudo bash install.sh"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --token) GH_TOKEN="$2"; shift 2 ;;
        *) die "Argumento desconocido: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
step "Docker"
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    info "Ya instalado: $(docker --version)"
else
    info "Instalando Docker Engine..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker || true
    info "Docker instalado: $(docker --version)"
fi

# Esperar a que el daemon esté listo (hasta 30s)
echo -n "  Esperando Docker daemon"
for i in $(seq 1 15); do
    docker info &>/dev/null && break
    echo -n "."
    sleep 2
done
echo
docker info &>/dev/null || die "Docker daemon no responde. Revisa: journalctl -xeu docker.service"

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
step "Ollama"
if ! command -v ollama &>/dev/null; then
    info "Instalando Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
fi
systemctl enable --now ollama
info "Descargando modelo $OLLAMA_MODEL (puede tardar varios minutos)..."
ollama pull "$OLLAMA_MODEL"
info "Modelo listo."

# ---------------------------------------------------------------------------
# Repositorio
# ---------------------------------------------------------------------------
step "Repositorio Yomitsu"
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Ya clonado en $INSTALL_DIR — actualizando..."
    git -C "$INSTALL_DIR" pull
else
    if [[ -n "$GH_TOKEN" ]]; then
        CLONE_URL="https://${GH_TOKEN}@github.com/saulcr59/Yomitsu.git"
    else
        CLONE_URL="$REPO_URL"
    fi
    info "Clonando en $INSTALL_DIR..."
    git clone "$CLONE_URL" "$INSTALL_DIR"
fi

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
step "Configuracion"
if [[ -f "$INSTALL_DIR/.env" ]]; then
    info ".env ya existe, no se modifica."
else
    echo
    read -rp "  OPENAI_API_KEY (para el analisis gramatical): " api_key
    [[ -n "$api_key" ]] || die "La API key no puede estar vacia."
    echo "OPENAI_API_KEY=$api_key" > "$INSTALL_DIR/.env"
    info ".env creado."
fi

# ---------------------------------------------------------------------------
# Servicios
# ---------------------------------------------------------------------------
step "Levantando servicios"
cd "$INSTALL_DIR"
docker compose up --build -d
info "Servicios iniciados."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo "------------------------------------------------------------"
echo " Yomitsu instalado en $INSTALL_DIR"
echo
echo " Puertos:"
echo "   8000  dictionary-service"
echo "   8001  translator-service"
echo "   8002  orchestrator-service  <-- el que usa el plugin Lua"
echo "   8003  grammar-analysis-service"
echo
echo " Comandos utiles:"
echo "   cd $INSTALL_DIR"
echo "   docker compose logs -f          # ver logs en vivo"
echo "   docker compose restart          # reiniciar todo"
echo "   docker compose down             # apagar"
echo "   docker compose up -d            # volver a levantar"
echo
echo " Recuerda actualizar ORCHESTRATOR_URL en el plugin Lua"
echo " con la IP LAN de esta maquina."
echo "------------------------------------------------------------"
