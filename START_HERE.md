# START HERE (beginner guide, Ubuntu 22.04 - the recommended way)

Paste each block into the Terminal. In the Ubuntu terminal paste with **Ctrl+Shift+V** (not Ctrl+V).
Press Enter after pasting. Wait until the prompt comes back before pasting the next block.

1. Open Terminal: press Ctrl+Alt+T (if Ubuntu runs inside Windows: Start menu -> "Ubuntu").

2. One-time tools (asks for your password; nothing shows while you type it, that is normal):
       sudo apt update && sudo apt install -y python3 python3-venv python3-pip unzip

3. Unpack the project:
       cp ~/Downloads/overrun-monitor.zip ~/ 2>/dev/null || cp /mnt/c/Users/*/Downloads/overrun-monitor.zip ~/
       cd ~ && unzip -q -o overrun-monitor.zip && cd overrun-monitor

4. Quick test drive (a few minutes) - must end with the words SMOKE RUN OK:
       bash setup_and_run.sh smoke

5. The real run (15-45 minutes; keep the laptop plugged in and the window open):
       bash setup_and_run.sh full

   It must end with the words ALL CHECKS PASSED.

6. Look at the figures:
       xdg-open figures

If anything says FAIL or shows an error: copy the last 40 lines of the terminal and send them to Claude.

## MacBook instead (backup option)
Open Terminal (Cmd+Space, type Terminal, Enter), then:
       xcode-select --install        # a window appears: click Install, wait, then continue
       cd ~/Downloads && unzip -q -o overrun-monitor.zip && cd overrun-monitor    # skip the unzip if a folder overrun-monitor is already there
       bash setup_and_run.sh smoke
       bash setup_and_run.sh full
