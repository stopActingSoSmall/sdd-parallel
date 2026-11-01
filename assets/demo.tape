# VHS Tape for sdd-parallel Terminal Screencast
# Syntax and options: https://github.com/charmbracelet/vhs

Output assets/demo.gif
Output docs/assets/demo.gif

Set Shell "bash"
Set FontSize 14
Set FontFamily "JetBrains Mono"
Set Width 1280
Set Height 720
Set Padding 20
Set Theme "Catppuccin Mocha"
Set Framerate 30
Set PlaybackSpeed 1.0
Set TypingSpeed 40ms

# Step 1: Standalone Merge-Bomb & 5-Kit SDD Verification (<5 sec, zero API keys)
Type "uvx sdd-parallel demo --kit all"
Sleep 500ms
Enter
Sleep 6s

# Step 2: Compute Blast-Radius Wave Plan with Kahn Acyclicity & Semantic Collisions
Type "sdd-parallel plan --kit auto"
Sleep 500ms
Enter
Sleep 4s

# Step 3: Provision Isolated Worktree Slots (track-a, track-b) with Clean Git Status
Type "sdd-parallel prepare --wave 1"
Sleep 500ms
Enter
Sleep 3s

# Step 4: Generate Standalone Interactive Wave Inspector UI
Type "sdd-parallel inspect --output assets/inspector.html"
Sleep 500ms
Enter
Sleep 3s
