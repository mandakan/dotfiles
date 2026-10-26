# Dotfiles

Personal dotfiles for macOS and Ubuntu, managed with [chezmoi](https://www.chezmoi.io).
Originally based on [Dries Vints](https://github.com/driesvints/dotfiles).

This repo is public. Anything specific to one machine (internal hostnames,
remotes, environment ids, local paths) lives in untracked files on that
machine, never here. See [Per-host settings](#per-host-settings).

## What's in here

```
.
├── .chezmoiroot              # tells chezmoi the source state is home/
├── install                   # idempotent bootstrap script
├── bin/
│   └── capture-claude-settings   # fold live settings edits into the host overlay
└── home/                     # chezmoi source state, rendered into $HOME
    ├── dot_zshrc             # zsh + Oh My Zsh + powerlevel10k config
    ├── dot_p10k.zsh          # powerlevel10k prompt tuning
    ├── dot_nanorc.tmpl       # nano config, platform-specific syntax include
    ├── Brewfile              # formulas, casks, mas, vscode extensions
    ├── .claude-settings/     # layers for ~/.claude/settings.json
    │   ├── base.json         #   every host
    │   └── darwin.json       #   macOS only
    └── dot_claude/           # Claude Code config: settings template,
                              # statusline, hooks, skills, CLAUDE.md
```

## Setup

1. macOS only: install the Command Line Tools with `xcode-select --install`.
2. Clone this repo (anywhere, `~/dotfiles` is the convention):
   ```sh
   git clone git@github.com:mandakan/dotfiles.git ~/dotfiles
   ```
3. Run the installer:
   ```sh
   cd ~/dotfiles && ./install
   ```

The installer is idempotent, so re-run it anytime. It installs the system
packages, Oh My Zsh and plugins, the Claude Code CLI and chezmoi, runs `brew
bundle` on macOS, then runs `chezmoi init --source ~/dotfiles` and `chezmoi
apply` to render `home/` into `$HOME`. chezmoi asks before it overwrites a file
that changed since it last wrote it.

## Day to day

```sh
chezmoi diff            # what apply would change
chezmoi apply           # render the repo into $HOME
chezmoi edit ~/.zshrc   # edit the source file, then apply
chezmoi cd              # shell in the repo; commit and push from there
```

Files are copies, not symlinks. Edit the source under `home/` (or use `chezmoi
edit`), then `chezmoi apply`. After pulling on another machine, run `chezmoi
apply` there.

### Brew bundle

```sh
# Capture newly-installed brew packages into the Brewfile
brew bundle dump --force --describe --file=~/dotfiles/home/Brewfile

# Preview what brew would remove (anything installed but not in Brewfile)
brew bundle cleanup --file=~/dotfiles/home/Brewfile

# Actually remove them
brew bundle cleanup --file=~/dotfiles/home/Brewfile --force
```

## Per-host settings

`~/.claude/settings.json` is rendered from three layers, later ones winning:

| Layer | File | Committed |
|---|---|---|
| 1. every host | `home/.claude-settings/base.json` | yes |
| 2. per OS | `home/.claude-settings/darwin.json` or `linux.json` | yes |
| 3. this host | `~/.config/chezmoi/claude-settings.local.json` | **no** |

Objects merge key by key, arrays are combined without duplicates, and a scalar
(such as `model`) from a later layer replaces an earlier one. Layer 3 is where
anything private goes, for example an `autoMode` block that describes internal
infrastructure. Claude Code only reads `autoMode` from user settings, which is
why it can't live in a project's own `.claude/settings.local.json`.

Shell additions that only make sense on one machine (installer snippets,
machine paths) go in `~/.zshrc.local`, which `.zshrc` sources if it exists.

### When Claude Code changes the settings

`/model`, `/config`, plugin toggles and auto mode setup write straight to
`~/.claude/settings.json`. The next `chezmoi apply` would undo them, and
chezmoi warns you before it does. To keep such a change:

- **This host only:** run `~/dotfiles/bin/capture-claude-settings`. It writes
  the difference between the live file and layers 1-2 into layer 3. Add
  `--dry-run` to preview.
- **Every host:** edit `home/.claude-settings/base.json` (or the OS layer),
  commit, then `chezmoi apply`.

`chezmoi diff` shows whether the live file has drifted.

### iTerm2 (macOS)

`~/.config/iterm2/com.googlecode.iterm2.plist` is rendered from
`home/dot_config/iterm2/`, and a chezmoi script points iTerm2 at that folder
("Load preferences from a custom folder", save changes: never) and installs
the MesloLGS NF fonts the Default profile uses. Quit iTerm2 before
the first apply (or re-run `chezmoi apply` after quitting it): a running
iTerm2 saves its in-memory settings over the file as soon as the folder is
enabled, and only reads the folder at launch.

iTerm2 reads that file at launch and does not write it on its own. To keep a
GUI change on every host, push it to the file first (Settings > General >
Settings > "Save Now"), then:

```sh
chezmoi re-add ~/.config/iterm2/com.googlecode.iterm2.plist
chezmoi cd && git commit -am "iterm2: ..." && git push
```

To discard a GUI change, restart iTerm2. If the file itself drifted, run
`chezmoi apply` first. Keys prefixed `NoSync`,
window frames and Sparkle updater state are host-local and are not in the repo.

### Migrating a host from the old symlink layout

Before this repo moved to chezmoi, `install` symlinked files into the checkout.
A host with uncommitted edits in the old `claude/settings.json` should capture
them before pulling. Move host-only lines in the old `.zshrc` into `~/.zshrc.local`
first, so `git pull` has nothing to conflict with:

```sh
cd ~/dotfiles && git fetch
tmp=$(mktemp -d) && git worktree add --detach "$tmp" origin/main
"$tmp/bin/capture-claude-settings" --dry-run   # review what it would keep
"$tmp/bin/capture-claude-settings"
git worktree remove "$tmp"
git checkout -- claude/settings.json && git pull && ./install
```

`install` removes the old symlinks that pointed into the repo and lets chezmoi
write real files in their place.

## Claude Code extras

`home/dot_claude/` ships a custom statusline (`statusline.sh`) with:

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

- macOS (Apple Silicon, some Homebrew paths assume `/opt/homebrew`) or Ubuntu
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
