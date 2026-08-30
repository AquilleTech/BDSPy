"""
BDSPy -- a Minecraft Bedrock Dedicated Server written from scratch in pure
Python (no external dependencies). Run in Termux: python3 main.py

Edit config.toml for port, MOTD, ground layers, starting items, view radius.
"""

from server.server import main

if __name__ == "__main__":
    main()
