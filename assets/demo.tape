# VHS Tape for sdd-parallel Terminal Screencast (ADR-0008 Callout-First Paged Storyboard)
# Syntax and options: https://github.com/charmbracelet/vhs

Output assets/demo.gif
Output docs/assets/demo.gif

Set Shell "bash"
Set FontSize 16
Set FontFamily "JetBrains Mono"
Set Width 1280
Set Height 720
Set Padding 20
Set Theme "Catppuccin Mocha"
Set Framerate 30
Set PlaybackSpeed 1.0
Set TypingSpeed 35ms

# Setup: Scaffold sandbox silently in background
Hide
Type "sdd-parallel demo --kit spec-kit --scaffold-only --output-dir /tmp/sdd-demo && cd /tmp/sdd-demo"
Enter
Sleep 1s
Type "clear"
Enter
Show

# Screen 1: AST Scan & Hidden Dependency Trap
Type "sdd-parallel demo --stage 1"
Sleep 500ms
Enter
Sleep 6.5s

Hide
Type "clear"
Enter
Show

# Screen 2: sdd-parallel plan — Naive Crash vs Wave 1 / Wave 2 Split
Type "sdd-parallel plan"
Sleep 500ms
Enter
Sleep 6.5s

Hide
Type "clear"
Enter
Show

# Screen 3: sdd-parallel prepare --wave 1 — Clean Worktrees & Scope Guard DENY
Type "sdd-parallel prepare --wave 1"
Sleep 500ms
Enter
Sleep 6.5s

Hide
Type "clear"
Enter
Show

# Screen 4: sdd-parallel merge --wave 1 — Conflict-Free tasks.md Merge & 5-Kit Callout
Type "sdd-parallel merge --wave 1"
Sleep 500ms
Enter
Sleep 6.5s
