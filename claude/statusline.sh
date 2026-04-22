#!/bin/bash
# Claude Code statusline
# Reads session JSON on stdin, prints one status line to stdout.
#
# Layout:
#   🌅 📁 dir 🌿 branch ↑2 ● | ⚡ Model 💰 $0.42 ($3.1/h) ⏱ 8m12s +124/-37 🧠 45k/200k (22%)

export LC_NUMERIC=C

# ──────────────────────────────────────────────────────────────────
# 1. Parse input — one jq call, tab-separated fields
# ──────────────────────────────────────────────────────────────────

input=$(cat)

IFS=$'\t' read -r \
    MODEL CURRENT_DIR PROJECT_DIR \
    COST DURATION_MS API_MS \
    LINES_ADD LINES_DEL \
    CTX_TOKENS CTX_MAX CTX_PCT CTX_THRESHOLD \
    EXCEEDS_200K OUTPUT_STYLE \
    < <(jq -r '[
        .model.display_name                                       // "",
        .workspace.current_dir                                    // "",
        .workspace.project_dir                                    // "",
        (.cost.total_cost_usd                                     // 0),
        (.cost.total_duration_ms                                  // 0),
        (.cost.total_api_duration_ms                              // 0),
        (.cost.total_lines_added                                  // 0),
        (.cost.total_lines_removed                                // 0),
        ((.context_window.current_usage.input_tokens             // 0)
       + (.context_window.current_usage.cache_creation_input_tokens // 0)
       + (.context_window.current_usage.cache_read_input_tokens     // 0)),
        (.context_window.context_window_size                      // 0),
        (.context_window.used_percentage                          // 0),
        (.context_window.autocompact_threshold                    // 0),
        (.exceeds_200k_tokens                                     // false),
        (.output_style.name                                       // "")
    ] | @tsv' <<<"$input")

# ──────────────────────────────────────────────────────────────────
# 2. Color helpers
# ──────────────────────────────────────────────────────────────────

# Respect the NO_COLOR convention (https://no-color.org): if the env var is
# set to any non-empty value, suppress all ANSI styling. `c()` and `rgb()`
# become no-ops so the rest of the script doesn't need conditionals.
if [[ -n ${NO_COLOR:-} ]]; then
    RESET=""; BOLD=""
    c()   { :; }
    rgb() { :; }
else
    RESET=$'\033[0m'
    BOLD=$'\033[1m'
    c()   { printf '\033[%sm' "$1"; }                        # SGR code
    rgb() { printf '\033[38;2;%d;%d;%dm' "$1" "$2" "$3"; }   # truecolor fg
fi

# Accessible palette — tuned for ≥3:1 contrast on both dark and light terminal
# backgrounds (Solarized-derived). Color is reserved for semantic signal;
# structural text (separator, burn rate, duration) stays in a neutral tone.
# All values expand to empty strings when NO_COLOR is set.
MUTED=$(rgb 147 147 147)     # neutral gray, readable on black AND white
YELLOW=$(rgb 181 137   0)
GREEN=$(rgb  133 153   0)
RED=$(rgb    220  50  47)
CYAN=$(rgb    42 161 152)
BLUE=$(rgb    38 139 210)
VIOLET=$(rgb 108 113 196)

# ──────────────────────────────────────────────────────────────────
# 3. Directory (project/subdir, or just basename)
# ──────────────────────────────────────────────────────────────────

if [[ $CURRENT_DIR == "$PROJECT_DIR" ]]; then
    DIR_DISPLAY="${CURRENT_DIR##*/}"
else
    DIR_DISPLAY="${PROJECT_DIR##*/}/${CURRENT_DIR##*/}"
fi

# ──────────────────────────────────────────────────────────────────
# 4. Git — branch + ahead/behind + dirty (single porcelain=v2 call)
# ──────────────────────────────────────────────────────────────────

GIT_DISPLAY=""
if git rev-parse --git-dir >/dev/null 2>&1; then
    git_info=$(git status --porcelain=v2 --branch 2>/dev/null)
    branch=$(awk '/^# branch.head/{print $3}' <<<"$git_info")
    ab_line=$(awk '/^# branch.ab/{print $3, $4}' <<<"$git_info")

    ahead=${ab_line% *}; ahead=${ahead#+}
    behind=${ab_line#* }; behind=${behind#-}
    ab_str=""
    [[ -n $ahead  && $ahead  -gt 0 ]] && ab_str+="↑$ahead"
    [[ -n $behind && $behind -gt 0 ]] && ab_str+="↓$behind"

    dirty=""
    grep -q '^[12?u]' <<<"$git_info" && dirty="●"

    case "$branch" in
        main|master|trunk)       git_icon="🌳" ;;
        wip/*|WIP/*)             git_icon="🚧" ;;
        hotfix/*|fix/*|bugfix/*) git_icon="🔥" ;;
        "")                      git_icon="🔗" ; branch="detached" ;;
        *)                       git_icon="🌿" ;;
    esac

    GIT_DISPLAY=" ${git_icon} ${YELLOW}${branch}${RESET}"
    [[ -n $ab_str ]] && GIT_DISPLAY+=" ${CYAN}${ab_str}${RESET}"
    [[ -n $dirty  ]] && GIT_DISPLAY+=" ${RED}${dirty}${RESET}"
fi

# ──────────────────────────────────────────────────────────────────
# 5. Model — color by tier
# ──────────────────────────────────────────────────────────────────

shopt -s nocasematch
case "$MODEL" in
    *opus*)   MODEL_COLOR="${BOLD}${VIOLET}" ;;
    *sonnet*) MODEL_COLOR="${BOLD}${BLUE}"   ;;
    *haiku*)  MODEL_COLOR="${BOLD}${GREEN}"  ;;
    *)        MODEL_COLOR="${BOLD}"          ;;   # default fg, just bold
esac
shopt -u nocasematch

# ──────────────────────────────────────────────────────────────────
# 6. Cost, duration, burn rate
# ──────────────────────────────────────────────────────────────────

# Cost: cents when <$1, dollars otherwise
if awk "BEGIN{exit !($COST < 1)}"; then
    COST_FMT=$(awk "BEGIN{printf \"%d¢\", $COST*100}")
else
    COST_FMT=$(awk "BEGIN{printf \"\$%.2f\", $COST}")
fi

# Duration: s / m+s / h+m
DURATION_SEC=$(( DURATION_MS / 1000 ))
if   (( DURATION_SEC >= 3600 )); then DUR_FMT="$((DURATION_SEC/3600))h$(((DURATION_SEC%3600)/60))m"
elif (( DURATION_SEC >= 60   )); then DUR_FMT="$((DURATION_SEC/60))m$((DURATION_SEC%60))s"
else                                  DUR_FMT="${DURATION_SEC}s"
fi

# Burn rate ($/hr). Suppress noise during the first 30s.
BURN=""
BURN_ICON="💰"
if (( DURATION_MS > 30000 )); then
    rate=$(awk "BEGIN{printf \"%.1f\", $COST * 3600000 / $DURATION_MS}")
    awk "BEGIN{exit !($rate > 20)}" && BURN_ICON="💸"
    BURN=" ${MUTED}\$${rate}/h${RESET}"
fi

# ──────────────────────────────────────────────────────────────────
# 7. Context — gradient colored against autocompact threshold
# ──────────────────────────────────────────────────────────────────

CTX_DISPLAY=""
if (( CTX_MAX > 0 )); then
    fmt_tok() {
        local n=$1
        if   (( n >= 1000000 )); then awk "BEGIN{printf \"%.1fM\", $n/1000000}"
        elif (( n >= 1000    )); then printf '%dk' $((n/1000))
        else                          printf '%d'  "$n"
        fi
    }
    tok_cur=$(fmt_tok "$CTX_TOKENS")
    tok_max=$(fmt_tok "$CTX_MAX")

    # Fraction of threshold consumed. threshold is fraction 0..1; fall back to 0.80.
    # frac=0 → green, frac=0.5 → yellow, frac=1 → red (past threshold clamps red).
    # Gradient endpoints from the accessible palette:
    # green(133,153,0) → yellow(181,137,0) → red(220,50,47).
    # All three clear 3:1 against both black and white terminal backgrounds.
    read -r r g b < <(awk -v pct="$CTX_PCT" -v thr="$CTX_THRESHOLD" 'BEGIN{
        if (thr <= 0) thr = 0.80
        f = pct / (thr * 100)
        if (f < 0) f = 0
        if (f > 1) f = 1
        if (f < 0.5) {
            t = f / 0.5
            r = 133 + (181 - 133) * t
            g = 153 + (137 - 153) * t
            b =   0 + (  0 -   0) * t
        } else {
            t = (f - 0.5) / 0.5
            r = 181 + (220 - 181) * t
            g = 137 + ( 50 - 137) * t
            b =   0 + ( 47 -   0) * t
        }
        printf "%d %d %d", r, g, b
    }')
    ctx_color=$(rgb "$r" "$g" "$b")

    ctx_icon="🧠"
    [[ $EXCEEDS_200K == "true" ]] && ctx_icon="🔥"

    CTX_DISPLAY=" ${ctx_color}${ctx_icon} ${tok_cur}/${tok_max} (${CTX_PCT}%)${RESET}"
fi

# ──────────────────────────────────────────────────────────────────
# 8. Lines changed this session
# ──────────────────────────────────────────────────────────────────

LINES_DISPLAY=""
if (( LINES_ADD > 0 || LINES_DEL > 0 )); then
    LINES_DISPLAY=" ${GREEN}+${LINES_ADD}${RESET}/${RED}-${LINES_DEL}${RESET}"
fi

# ──────────────────────────────────────────────────────────────────
# 9. Time-of-day emoji
# ──────────────────────────────────────────────────────────────────

hour=$(date +%-H)
if   (( hour >=  5 && hour <  8 )); then TOD="🌅"
elif (( hour >=  8 && hour < 12 )); then TOD="☀️ "
elif (( hour >= 12 && hour < 17 )); then TOD="🌤 "
elif (( hour >= 17 && hour < 20 )); then TOD="🌆"
elif (( hour >= 20 && hour < 23 )); then TOD="🌙"
else                                     TOD="🌌"
fi

# ──────────────────────────────────────────────────────────────────
# 10. Assemble
# ──────────────────────────────────────────────────────────────────

SEP=" ${MUTED}|${RESET} "

out=""
out+="${TOD} "
out+="📁 ${YELLOW}${DIR_DISPLAY}${RESET}"
out+="${GIT_DISPLAY}"
out+="${SEP}"
out+="${MODEL_COLOR}⚡ ${MODEL}${RESET}"
out+=" ${BURN_ICON} ${GREEN}${COST_FMT}${RESET}${BURN}"
out+=" ⏱  ${DUR_FMT}"
out+="${LINES_DISPLAY}"
out+="${CTX_DISPLAY}"

printf '%s\n' "$out"
