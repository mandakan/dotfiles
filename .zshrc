# Homebrew (Apple Silicon) -- guarded so this file works on Linux hosts too.
[[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"

# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# If you come from bash you might have to change your $PATH.
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"

# Path to your oh-my-zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time oh-my-zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
# jonathan
# robbyrussell
# obraun
ZSH_THEME="powerlevel10k/powerlevel10k"

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
# zstyle ':omz:update' mode auto      # update automatically without asking
zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(
  git macos docker docker-compose iterm2 tmux vscode brew aliases gh
  z nvm extract colored-man-pages
  zsh-autosuggestions zsh-syntax-highlighting
)

source $ZSH/oh-my-zsh.sh

# User configuration

# export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Preferred editor — brew's nano is substantially newer than macOS's bundled
# /usr/bin/nano (2.0.6). $HOMEBREW_PREFIX is set by `brew shellenv` in
# /etc/zprofile, so we don't fork `brew --prefix` on every shell startup.
if [[ -n ${HOMEBREW_PREFIX:-} ]]; then
    export EDITOR="$HOMEBREW_PREFIX/bin/nano"
    export VISUAL="$EDITOR"
fi

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.
#
# Example aliases
# alias zshconfig="mate ~/.zshrc"
# alias ohmyzsh="mate ~/.oh-my-zsh"

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh

# Setup fzf completions. macOS brew fzf supports `fzf --zsh` (>= 0.48); Ubuntu's
# apt fzf (0.44 on 24.04) is older, so fall back to its packaged example files.
if command -v fzf >/dev/null 2>&1; then
    if fzf --zsh >/dev/null 2>&1; then
        source <(fzf --zsh)
    elif [[ -d /usr/share/doc/fzf/examples ]]; then
        source /usr/share/doc/fzf/examples/key-bindings.zsh
        source /usr/share/doc/fzf/examples/completion.zsh
    fi
fi

# Setup pay-respects alias (replaces thefuck; binds `f` by default)
if command -v pay-respects >/dev/null 2>&1; then
    eval "$(pay-respects zsh --alias)"
    # Workaround for "__pr_base:1: bad substitution" with powerlevel10k:
    # the generated hook runs `print -P "$PROMPT"`, which chokes on p10k's
    # prompt internals. Use a static prefix instead, same as the upstream
    # fix (iffse/pay-respects#133). Remove this override once a release
    # newer than 0.8.8 ships and switch to: pay-respects zsh --prompt-prefix '❯'
    function __pr_base() {
        _PR_MODE="$1" _PR_PREFIX="❯ " _PR_LAST_COMMAND="$2" _PR_ALIAS="`alias`" _PR_SHELL="zsh" "pay-respects"
    }

    # Weekly check for new pay-respects releases (omz-update-style toast).
    # Never blocks the prompt: shows the cached result from a previous check,
    # then refreshes the cache in a disowned background job at most once per
    # 7 days. Cache line format: "<epoch-of-check> <latest-version>".
    () {
        local cache="${XDG_CACHE_HOME:-$HOME/.cache}/pay-respects-update-check"
        local now=$(date +%s) last=0 latest=""
        [[ -r $cache ]] && read -r last latest < "$cache"

        local installed=$(pay-respects --version 2>/dev/null | awk '{print $2}')
        if [[ -n $latest && -n $installed && $latest != $installed ]]; then
            print -P "%F{yellow}pay-respects $latest is out (installed: $installed) -- run ~/dotfiles/install --check-updates for the bump checklist%f"
        fi

        if (( now - last > 7 * 24 * 3600 )); then
            {
                mkdir -p "${cache:h}"
                local tag=$(curl -fsSL --max-time 10 \
                    https://api.github.com/repos/iffse/pay-respects/releases/latest 2>/dev/null \
                    | sed -n 's/.*"tag_name": *"v\([0-9.][0-9.]*\)".*/\1/p')
                # On fetch failure keep the old version but still stamp the
                # time, so a dead network can't turn this into a per-shell hit.
                print "$now ${tag:-$latest}" > "$cache"
            } &> /dev/null &|
        fi
    }
fi

# Setup nvm
export NVM_DIR="$HOME/.nvm"
  [ -s "/opt/homebrew/opt/nvm/nvm.sh" ] && \. "/opt/homebrew/opt/nvm/nvm.sh"  # This loads nvm
  [ -s "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm" ] && \. "/opt/homebrew/opt/nvm/etc/bash_completion.d/nvm"  # This loads nvm bash_completion

# Added by LM Studio CLI (lms)
export PATH="$PATH:/Users/mathias/.cache/lm-studio/bin"

export ANDROID_HOME="$HOME/Library/Android/sdk"
export PATH="$ANDROID_HOME/emulator:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

# ffmpeg-full is keg-only (conflicts with the slim ffmpeg formula); prepend
# its bin so drawtext-capable ffmpeg is the host default (splitsmith overlays).
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:$PATH"
