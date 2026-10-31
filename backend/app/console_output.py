"""Bound platform service diagnostics without rotating/deleting existing logs."""


class LimitedConsole:
    def __init__(self, stream, limit=1024 * 1024):
        self.stream, self.remaining = stream, limit
        self.truncated = False

    def write(self, value):
        count = len(value)
        encoded = value.encode("utf8", errors="replace")
        if self.remaining:
            piece = encoded[: self.remaining].decode("utf8", errors="ignore")
            self.stream.write(piece)
            self.remaining -= min(len(encoded), self.remaining)
        if not self.remaining and not self.truncated:
            self.stream.write(
                "\n[platform diagnostic limit reached; durable business logs remain in project records]\n"
            )
            self.truncated = True
        self.stream.flush()
        return count

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False
