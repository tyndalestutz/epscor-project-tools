"""Cancelable manual setup steps shared by acquisition routines."""
def prepare_setup(message):
    response = input(message + "[Enter = ready, b = cancel] ").strip().lower()
    while response:
        if response in {"b", "back", "q", "quit", "exit"}:
            raise KeyboardInterrupt
        response = input("Press Enter when ready, or b to cancel: ").strip().lower()
