#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────────
# remote-clone.sh — one-command Hermes remote clone setup
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/demossml/hermes-agent/multi-agent/scripts/remote-clone.sh | bash -s -- --name <clone-name> --parent <tailscale-host>
#
# Options:
#   --name NAME       Clone name (required, e.g. "мама", "office-pc")
#   --parent HOST     Main agent's Tailscale hostname (required)
#   --token TOKEN     Pre-created Telegram bot token (optional)
#   --model MODEL     Default model (default: current)
#   --provider PROV   Default provider (default: current)
#   --help            Show this help
#
# What it does:
#   1. Installs Tailscale (if missing) — macOS + Linux
#   2. Authenticates to tailnet (interactive one-time)
#   3. Installs Hermes Agent from multi-agent branch
#   4. Creates a profile-clone with the given name
#   5. Registers the clone with the main agent via Tailscale SSH
#   6. Prints "Готово" with next steps
#
# Requirements: curl, bash, systemd (Linux) or launchd (macOS)
# No root required. User-level systemd via systemctl --user.
# ───────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}ℹ${NC} $*"; }
ok()    { echo -e "${GREEN}✅${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}❌${NC} $*"; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────
CLONE_NAME=""
PARENT_HOST=""
BOT_TOKEN=""
HERMES_MODEL="current"
HERMES_PROVIDER="current"
HERMES_BRANCH="multi-agent"
HERMES_REPO="https://github.com/demossml/hermes-agent.git"
HERMES_HOME="${HOME}/.hermes"
PROFILE_DIR="${HERMES_HOME}/profiles"
TAILSCALE_SSH_PORT=22

# ── Parse args ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)   CLONE_NAME="$2"; shift 2 ;;
        --parent) PARENT_HOST="$2"; shift 2 ;;
        --token)  BOT_TOKEN="$2"; shift 2 ;;
        --model)  HERMES_MODEL="$2"; shift 2 ;;
        --provider) HERMES_PROVIDER="$2"; shift 2 ;;
        --help|-h)
            head -24 "$0" | tail -20
            exit 0
            ;;
        *) err "Unknown flag: $1. Use --help." ;;
    esac
done

# ── Validate ──────────────────────────────────────────────────────
[[ -z "$CLONE_NAME" ]] && err "--name is required (e.g. --name мама)"
[[ -z "$PARENT_HOST" ]] && err "--parent is required (e.g. --parent my-server)"
# Sanitize clone name for filesystem
SAFE_NAME=$(echo "$CLONE_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/-/g')
[[ "$SAFE_NAME" != "$CLONE_NAME" ]] && info "Clone name sanitised: $CLONE_NAME → $SAFE_NAME"

# ── Platform detection ────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin)  PLATFORM="macos" ;;
    Linux)   PLATFORM="linux" ;;
    *)       err "Unsupported OS: $OS. Only macOS and Linux are supported." ;;
esac
info "Platform: $PLATFORM"

# ═══════════════════════════════════════════════════════════════════
# Step 1: Install Tailscale
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 1/6: Tailscale ═══${NC}"

if command -v tailscale &>/dev/null; then
    TS_VER=$(tailscale version | head -1)
    ok "Tailscale already installed: $TS_VER"
else
    info "Installing Tailscale..."
    case "$PLATFORM" in
        macos)
            if command -v brew &>/dev/null; then
                brew install tailscale 2>&1 | tail -1 || err "brew install tailscale failed"
            else
                # Direct .pkg install
                TS_PKG="/tmp/Tailscale-latest.pkg"
                curl -fsSLo "$TS_PKG" "https://pkgs.tailscale.com/stable/Tailscale-latest-macos.pkg"
                sudo installer -pkg "$TS_PKG" -target / 2>&1 || err "Tailscale .pkg install failed"
                rm -f "$TS_PKG"
            fi
            ;;
        linux)
            curl -fsSL https://tailscale.com/install.sh | sh 2>&1 || err "Tailscale install script failed"
            ;;
    esac
    ok "Tailscale installed"
fi

# ═══════════════════════════════════════════════════════════════════
# Step 2: Authenticate to tailnet
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 2/6: Tailscale auth ═══${NC}"

TS_STATUS=$(tailscale status --json 2>/dev/null || echo '{"BackendState":"NeedsLogin"}')
TS_STATE=$(echo "$TS_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','NeedsLogin'))" 2>/dev/null || echo "NeedsLogin")
TS_HOSTNAME=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('HostName',''))" 2>/dev/null || echo "")

if [[ "$TS_STATE" == "Running" ]]; then
    ok "Tailscale connected. Hostname: ${TS_HOSTNAME:-unknown}"
else
    warn "Tailscale needs authentication."
    info "Starting Tailscale daemon..."
    case "$PLATFORM" in
        macos)
            sudo tailscaled install-system-daemon 2>/dev/null || true
            sudo tailscaled start 2>/dev/null || true
            ;;
        linux)
            systemctl --user enable --now tailscaled 2>/dev/null || \
                sudo systemctl enable --now tailscaled 2>/dev/null || true
            ;;
    esac
    sleep 2

    info "Opening Tailscale login page..."
    tailscale up --accept-routes --operator="$USER" 2>&1

    # Wait for auth
    for i in $(seq 1 60); do
        TS_STATE=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','NeedsLogin'))" 2>/dev/null || echo "NeedsLogin")
        if [[ "$TS_STATE" == "Running" ]]; then
            break
        fi
        sleep 2
    done

    if [[ "$TS_STATE" == "Running" ]]; then
        TS_HOSTNAME=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('HostName',''))" 2>/dev/null || echo "")
        ok "Tailscale connected. Your hostname: ${TS_HOSTNAME:-unknown}"
    else
        err "Tailscale authentication timed out. Run 'tailscale up' manually and retry."
    fi
fi

# ── Enable Tailscale SSH ──────────────────────────────────────────
info "Ensuring Tailscale SSH is enabled..."
tailscale set --ssh 2>/dev/null || warn "Could not enable Tailscale SSH (non-fatal)"
ok "Tailscale ready"

# ═══════════════════════════════════════════════════════════════════
# Step 3: Install Hermes Agent
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 3/6: Hermes Agent ═══${NC}"

HERMES_INSTALL_DIR="${HOME}/hermes-agent"

if [[ -f "${HERMES_INSTALL_DIR}/run_agent.py" ]]; then
    ok "Hermes already installed at ${HERMES_INSTALL_DIR}"
    info "Updating..."
    cd "$HERMES_INSTALL_DIR"
    git fetch origin "$HERMES_BRANCH" 2>/dev/null || true
    git checkout "$HERMES_BRANCH" 2>/dev/null || true
    git pull origin "$HERMES_BRANCH" 2>/dev/null || true
else
    info "Cloning Hermes Agent (${HERMES_BRANCH} branch)..."
    git clone --branch "$HERMES_BRANCH" "$HERMES_REPO" "$HERMES_INSTALL_DIR" 2>&1 || \
        err "Failed to clone Hermes. Check internet connection."
fi

cd "$HERMES_INSTALL_DIR"

# ── Install Python dependencies ──────────────────────────────────
info "Installing Python dependencies..."
if command -v uv &>/dev/null; then
    uv pip install -e . 2>&1 | tail -3 || warn "uv install had warnings (non-fatal)"
elif command -v pip3 &>/dev/null; then
    pip3 install -e . 2>&1 | tail -3 || warn "pip3 install had warnings (non-fatal)"
else
    err "Neither uv nor pip3 found. Install Python 3.10+ first."
fi

ok "Hermes Agent installed"

# ═══════════════════════════════════════════════════════════════════
# Step 4: Create profile-clone
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 4/6: Profile clone ═══${NC}"

CLONE_PROFILE_DIR="${PROFILE_DIR}/${SAFE_NAME}"

if [[ -d "$CLONE_PROFILE_DIR" ]]; then
    ok "Profile '${SAFE_NAME}' already exists"
else
    info "Creating profile '${SAFE_NAME}'..."
    mkdir -p "$CLONE_PROFILE_DIR"

    # Create config.yaml for the clone
    cat > "${CLONE_PROFILE_DIR}/config.yaml" << YAML
# ── Hermes Clone Profile: ${CLONE_NAME} ──────────────────────
# Auto-generated by remote-clone.sh

model:
  default: "${HERMES_MODEL}"
  provider: "${HERMES_PROVIDER}"

agent:
  max_iterations: 10

terminal:
  backend: local
  timeout: 300

display:
  show_cost: true

# Allow parent agent to orchestrate this clone
profile:
  allow_orchestration: true
  parent_host: "${PARENT_HOST}"
  clone_name: "${CLONE_NAME}"
  created_by: remote-clone.sh
  created_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Multi-agent: enable delegation so clone can create sub-agents
platform_toolsets:
  cli:
    enabled_toolsets:
      - delegation
      - messaging
      - terminal
      - file
      - web_search
      - skills
YAML

    # Create .env template
    cat > "${CLONE_PROFILE_DIR}/.env" << ENV
# API keys for Hermes clone '${CLONE_NAME}'
# Fill in at least one provider below.

# OpenAI
# OPENAI_API_KEY=sk-...

# Anthropic
# ANTHROPIC_API_KEY=sk-ant-...

# DeepSeek
# DEEPSEEK_API_KEY=sk-...

# Telegram bot token (if using gateway)
# TELEGRAM_BOT_TOKEN=${BOT_TOKEN}

# OpenRouter (works with any model)
# OPENROUTER_API_KEY=sk-or-...
ENV

    # Create directories
    mkdir -p "${CLONE_PROFILE_DIR}/skills"
    mkdir -p "${CLONE_PROFILE_DIR}/memories"
    mkdir -p "${CLONE_PROFILE_DIR}/sessions"
    mkdir -p "${CLONE_PROFILE_DIR}/logs"

    ok "Profile '${SAFE_NAME}' created"
fi

# ═══════════════════════════════════════════════════════════════════
# Step 5: Register with main agent
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 5/6: Register with main agent ═══${NC}"

REGISTER_MSG=$(cat <<EOF
{
    "action": "register_clone",
    "clone_name": "${CLONE_NAME}",
    "clone_host": "$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('HostName','unknown'))" 2>/dev/null || hostname)",
    "clone_profile": "${SAFE_NAME}",
    "clone_home": "${CLONE_PROFILE_DIR}",
    "clone_ip": "$(tailscale ip -4 2>/dev/null || echo 'unknown')"
}
EOF
)

info "Contacting main agent at ${PARENT_HOST}..."
# Try to send registration to parent via Tailscale SSH
if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
       "${PARENT_HOST}" \
       "echo '${REGISTER_MSG}' >> ${HERMES_HOME}/remote-clones.jsonl && echo 'ok'" 2>/dev/null; then
    ok "Registered with main agent (${PARENT_HOST})"
else
    warn "Could not auto-register with main agent."
    warn "The main agent at '${PARENT_HOST}' may be offline."
    warn "Registration will be retried next time this clone starts."
    warn "Or run manually on the main agent:"
    echo ""
    echo -e "  ${CYAN}cat >> ~/.hermes/remote-clones.jsonl << 'EOF'${NC}"
    echo "$REGISTER_MSG"
    echo -e "  ${CYAN}EOF${NC}"
    echo ""
fi

# ═══════════════════════════════════════════════════════════════════
# Step 6: Start clone as background service
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══ Step 6/6: Start clone service ═══${NC}"

if [[ -n "$BOT_TOKEN" ]]; then
    info "Setting up Telegram bot for clone..."
    # Write token to profile .env
    if grep -q "TELEGRAM_BOT_TOKEN" "${CLONE_PROFILE_DIR}/.env" 2>/dev/null; then
        sed -i '' "s/# TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=${BOT_TOKEN}/" "${CLONE_PROFILE_DIR}/.env" 2>/dev/null || \
        sed -i "s/# TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=${BOT_TOKEN}/" "${CLONE_PROFILE_DIR}/.env" 2>/dev/null
    fi

    # Start gateway for this profile
    info "Starting gateway for profile '${SAFE_NAME}'..."
    cd "$HERMES_INSTALL_DIR"
    HERMES_HOME="${CLONE_PROFILE_DIR}" hermes gateway install --profile "${SAFE_NAME}" 2>/dev/null || \
        warn "Gateway install failed. Start manually: HERMES_HOME=${CLONE_PROFILE_DIR} hermes gateway run"
else
    info "No --token provided. Clone will run in CLI mode only."
    info "To add a Telegram bot later, edit: ${CLONE_PROFILE_DIR}/.env"
    info "Then run: HERMES_HOME=${CLONE_PROFILE_DIR} hermes gateway install"
fi

# ═══════════════════════════════════════════════════════════════════
# Done
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  ✅  Clone '${CLONE_NAME}' is ready!${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Profile:${NC}  ${SAFE_NAME}"
echo -e "  ${BOLD}Home:${NC}     ${CLONE_PROFILE_DIR}"
echo -e "  ${BOLD}Config:${NC}   ${CLONE_PROFILE_DIR}/config.yaml"
echo -e "  ${BOLD}Env:${NC}      ${CLONE_PROFILE_DIR}/.env"
echo ""
echo -e "  ${BOLD}Next steps on the MAIN agent:${NC}"
echo -e "    ${CYAN}hermes remote list${NC}                  # see this clone"
echo -e "    ${CYAN}hermes remote setup ${SAFE_NAME}${NC}         # configure clone"
echo -e "    ${CYAN}hermes remote memory ${SAFE_NAME}${NC}       # read clone's memory"
echo -e "    ${CYAN}hermes remote tools ${SAFE_NAME} enable web${NC}  # enable tools"
echo -e "    ${CYAN}hermes remote exec ${SAFE_NAME} \"...\"${NC}    # send a task"
echo ""
echo -e "  ${BOLD}On THIS machine:${NC}"
echo -e "    Edit ${CYAN}${CLONE_PROFILE_DIR}/.env${NC} to add API keys"
echo -e "    ${CYAN}HERMES_HOME=${CLONE_PROFILE_DIR} hermes${NC}  # interactive mode"
echo ""
echo -e "  ${BOLD}To stop:${NC}"
echo -e "    ${CYAN}HERMES_HOME=${CLONE_PROFILE_DIR} hermes gateway stop${NC}"
echo ""
