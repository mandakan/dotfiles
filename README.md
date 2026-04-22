# Dotfiles

Personal macOS dotfiles. Originally based on [Dries Vints](https://github.com/driesvints/dotfiles).

## What's in here

```
.
├── .zshrc             # zsh + Oh My Zsh + powerlevel10k config
├── .p10k.zsh          # powerlevel10k prompt tuning
├── .nanorc            # nano editor config
├── Brewfile           # formulas, casks, mas, vscode extensions
├── install            # idempotent bootstrap script
└── claude/            # Claude Code config (statusline, hooks, settings)
    ├── settings.json
    ├── statusline.sh
    ├── CLAUDE.md
    ├── RTK.md
    └── hooks/
        └── rtk-rewrite.sh
```

## Setup

1. Install macOS Command Line Tools:
   ```sh
   xcode-select --install
   ```
2. Clone this repo (anywhere — `~/dotfiles` or `~/.dotfiles` both work):
   ```sh
   git clone git@github.com:mandakan/dotfiles.git ~/dotfiles
   ```
3. Run the installer:
   ```sh
   cd ~/dotfiles && ./install
   ```

The installer is idempotent — re-run it anytime to reconcile drift. Anything it
would overwrite is moved into `~/.dotfiles-backup/<timestamp>/` first.

## What the installer does

1. Installs Homebrew if missing.
2. Installs Oh My Zsh non-interactively (won't change your login shell — do that
   manually with `chsh -s $(which zsh)` if needed).
3. Clones powerlevel10k, `zsh-autosuggestions`, and `zsh-syntax-highlighting`
   into `$ZSH_CUSTOM`.
4. Symlinks `.zshrc`, `.p10k.zsh`, `.nanorc`, and `Brewfile` into `$HOME`.
5. Symlinks the `claude/` directory contents into `~/.claude/` so Claude Code
   picks up the custom statusline, hooks, and settings.
6. Runs `brew bundle` against the Brewfile.

## Keeping it in sync

```sh
# Capture newly-installed brew packages into the Brewfile
brew bundle dump --force --describe --file=~/dotfiles/Brewfile

# Preview what brew would remove (anything installed but not in Brewfile)
brew bundle cleanup --file=~/dotfiles/Brewfile

# Actually remove them
brew bundle cleanup --file=~/dotfiles/Brewfile --force
```

## Claude Code extras

The `claude/` subdirectory ships a custom statusline (`statusline.sh`) with:

- time-of-day indicator
- directory + git branch with ahead/behind and dirty state
- model name colored by tier (Opus / Sonnet / Haiku)
- cost in cents under $1, dollars above; burn-rate warning at >$20/hr
- context usage with a truecolor gradient against the autocompact threshold
- lines added/removed this session
- `NO_COLOR` support and a Solarized-derived palette tuned for both dark and
  light terminal backgrounds

Requirements: `jq`, bash 4+, truecolor-capable terminal (iTerm2, Alacritty,
WezTerm, kitty, modern Terminal.app fork, etc.). Apple Terminal renders the
gradient as a flat color — it degrades gracefully.

## Requirements

- macOS (Apple Silicon — some Homebrew paths assume `/opt/homebrew`)
- Git (bundled with Xcode CLT)
- Internet connection on first run

## Contributing / repo hygiene

This repo uses [`pre-commit`](https://pre-commit.com) to run:

- [**gitleaks**](https://github.com/gitleaks/gitleaks) — scans every diff for
  leaked API keys, tokens, and other secrets. A full-history scan also runs in
  CI.
- [**shellcheck**](https://www.shellcheck.net) — catches shell-script bugs
  (quoting, globbing, dead branches).
- Standard hygiene hooks — trailing whitespace, missing newlines, large files,
  mis-flagged executables, merge conflict markers.

The `install` script installs the hooks automatically if `pre-commit` is
present. To wire them up manually after cloning:

```sh
brew install pre-commit
cd ~/dotfiles
pre-commit install
pre-commit run --all-files    # optional, scan the whole repo right now
```

Commits that introduce secrets will be blocked locally, and any that slip
through (`git commit --no-verify`) are caught by the GitHub Actions workflow
in `.github/workflows/ci.yml` before merge.

## License

[MIT](LICENSE)
