last_known: dict | None = None


def set_location(latitude: float, longitude: float, label: str | None = None) -> None:
    global last_known
    last_known = {"latitude": latitude, "longitude": longitude, "label": label}


def get_location() -> dict | None:
    return last_known
