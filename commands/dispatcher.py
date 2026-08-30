"""A small command registry. Not wired into anything fancy yet -- register
handlers with @dispatcher.command('name'), and feed raw chat text starting
with '/' to dispatcher.handle(player, text)."""


class CommandDispatcher:
    def __init__(self):
        self._handlers = {}

    def command(self, name):
        def decorator(fn):
            self._handlers[name] = fn
            return fn
        return decorator

    def handle(self, player, text):
        if not text.startswith("/"):
            return False
        parts = text[1:].split()
        if not parts:
            return False
        name, args = parts[0], parts[1:]
        handler = self._handlers.get(name)
        if handler is None:
            return False
        handler(player, args)
        return True


dispatcher = CommandDispatcher()


@dispatcher.command("help")
def _help(player, args):
    names = ", ".join(sorted(dispatcher._handlers.keys()))
    print(f"[commands] {player.display_name} ran /help -- available: {names}")
