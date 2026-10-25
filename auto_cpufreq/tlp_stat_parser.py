class TLPStatusParser:
    """Read tlp-stat -s output across legacy and current TLP versions."""

    def __init__(self, tlp_stat_output):
        self.data = {}
        self._parse(tlp_stat_output)

    def _parse(self, data):
        for line in data.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                self.data[key.strip().lower()] = value.strip()

    def state(self):
        """Return enabled, disabled or unknown; never guess from absent data."""
        values = []
        for key in ("tlp", "state"):
            if key not in self.data:
                continue
            token = self.data[key].split(",", 1)[0].strip().lower()
            if token not in ("enabled", "disabled"):
                return "unknown"
            values.append(token)
        if not values or len(set(values)) != 1:
            return "unknown"
        return values[0]

    def is_enabled(self):
        return self.state() == "enabled"
