#!/usr/bin/env python3
"""Показывает, что терминал реально шлёт при нажатии клавиш (с kitty keyboard protocol).
Запускать в обычном окне терминала, вне herdr: иначе herdr перехватит сочетание раньше. Выход — Ctrl+C."""
import sys, tty, termios, os

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
sys.stdout.write("\x1b[>1u")   # kitty keyboard protocol, disambiguate escape codes
sys.stdout.flush()
print("Нажимайте клавиши (в т.ч. с русской раскладкой). Выход: Ctrl+C\r")
try:
    tty.setraw(fd)
    while True:
        b = os.read(fd, 64)
        if not b:
            break
        pretty = b.decode("utf-8", "replace").replace("\x1b", "<ESC>")
        print(f"bytes={b!r}  text={pretty!r}\r")
        if b == b"\x03":
            break
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    sys.stdout.write("\x1b[<u")
    sys.stdout.flush()
    print()
