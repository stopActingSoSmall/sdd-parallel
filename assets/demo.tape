# VHS Tape for sdd-parallel Terminal Screencast
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

# Scene 1: Standalone 5-Kit SDD Verification (<4 sec, zero API keys)
Type "sdd-parallel demo --kit all"
Sleep 500ms
Enter
Sleep 12s

# Scene 2: Historical Benchmark Evaluation (100% Conflict Recall, 0 FN)
Type "sdd-parallel bench --demo"
Sleep 500ms
Enter
Sleep 10s

# Scene 3: Clean Worktree Provisioning & Blast-Radius Wave Scheduling
Type "sdd-parallel demo --kit spec-kit --scaffold-only --output-dir /tmp/sdd-demo && cd /tmp/sdd-demo"
Sleep 500ms
Enter
Sleep 4s

Type "sdd-parallel plan"
Sleep 500ms
Enter
Sleep 7s

Type "sdd-parallel prepare --wave 1"
Sleep 500ms
Enter
Sleep 7s
