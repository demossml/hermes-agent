# ───────────────────────────────────────────────────────────
# Hermes Agent — умное переключение проектов при cd
# 
# Добавь этот блок в ~/.zshrc (или ~/.bashrc для bash).
# После этого `cd` в любую папку проекта Hermes будет
# автоматически активировать этот проект.
#
# Установка:
#   echo 'source ~/.hermes/hermes-agent/scripts/hermes_cd_hook.sh' >> ~/.zshrc
#   source ~/.zshrc
# ───────────────────────────────────────────────────────────

# Путь к Python-скрипту детекции
_HERMES_CD_HOOK="$HOME/.hermes/hermes-agent/scripts/hermes_cd_hook.py"

hermes_cd_hook() {
    # Пропускаем, если скрипта нет
    [ -f "$_HERMES_CD_HOOK" ] || return 0

    # Только если мы в интерактивном режиме
    [[ -o interactive ]] || return 0

    # Запускаем детектор (быстрый, <100ms)
    python3 "$_HERMES_CD_HOOK" 2>/dev/null
}

# ── Zsh — переопределяем cd ──────────────────────────────
# Сохраняем оригинальный cd
if [[ -n "$ZSH_VERSION" ]]; then
    # chpwd хук срабатывает после СМЕНЫ директории
    autoload -Uz add-zsh-hook
    add-zsh-hook chpwd hermes_cd_hook
fi

# ── Bash — переопределяем cd (через функцию) ─────────────
if [[ -n "$BASH_VERSION" ]]; then
    cd() {
        builtin cd "$@" && hermes_cd_hook
    }
fi