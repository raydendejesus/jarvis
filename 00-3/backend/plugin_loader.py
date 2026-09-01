"""
Discovers and registers plugins from backend/plugins/ at startup. A plugin is
a single .py file in that folder exposing some subset of:

  PLUGIN_NAME (str)            - shown in the dashboard's plugin list
  TOGGLE_LABEL (str, optional) - human-readable toggle text; defaults to PLUGIN_NAME
  CONFIG_KEY (str, optional)   - the config.json key gating this plugin. Omit
                                  entirely for an always-on plugin with no
                                  toggle (like the core web_search tool).
  ENABLED_BY_DEFAULT (bool)    - default toggle state if CONFIG_KEY is set; default False
  SCHEMAS (list[dict])         - tool schemas, same OpenAI function-calling
                                  format the core tools use
  DISPATCH (dict[str, callable]) - tool name -> async handler taking one
                                  `args: dict` parameter and returning a string
  RELATED_CONNECTION (str, optional) - the name of an entry in server.py's
                                  /api/connections list this plugin needs
                                  signed in (e.g. "google") - lets the
                                  dashboard group this plugin with its
                                  connection automatically instead of you
                                  having to wire that grouping up by hand.
  VRAM_COST (str, optional)     - shown next to this plugin's toggle, e.g.
                                  "no local model" or "~6 GB when used".
                                  Defaults to "no local model" if omitted -
                                  most plugins are just network calls.

Dropping a well-formed file into plugins/ is the whole installation step - no
core file needs editing. A plugin that fails to import is skipped with a
logged warning rather than breaking every other plugin or the server itself.

Plugin tools are only ever offered on the dashboard/native-listener chat path
(tools.available_schemas), never on phone calls (tools.PHONE_TOOLS_OUTBOUND/
INBOUND, which are built from a small fixed list and never touch plugins at
all) - so a plugin author never needs to think about phone-call exposure;
it's excluded by construction, not by convention.
"""
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"


def _discover() -> list[ModuleType]:
    if not PLUGINS_DIR.exists():
        return []
    modules = []
    for info in pkgutil.iter_modules([str(PLUGINS_DIR)]):
        if info.name.startswith("_"):
            continue
        try:
            modules.append(importlib.import_module(f"plugins.{info.name}"))
        except Exception as exc:  # noqa: BLE001 - one bad plugin must never break the rest
            print(f"[plugin_loader] failed to load plugin '{info.name}': {exc}", flush=True)
    return modules


_loaded_plugins = _discover()


def plugin_metadata() -> list[dict]:
    """For the dashboard/tray to list installed plugins and their toggle state."""
    result = []
    for module in _loaded_plugins:
        config_key = getattr(module, "CONFIG_KEY", None)
        name = getattr(module, "PLUGIN_NAME", module.__name__.rsplit(".", 1)[-1])
        result.append({
            "name": name,
            "config_key": config_key,
            "label": getattr(module, "TOGGLE_LABEL", name),
            "always_on": config_key is None,
            "related_connection": getattr(module, "RELATED_CONNECTION", None),
            "vram_cost": getattr(module, "VRAM_COST", "no local model"),
        })
    return result


def config_defaults() -> dict:
    """Merged into config.py's DEFAULTS so a plugin's toggle exists with a
    sane default the moment its file is dropped in - no manual config.py edit."""
    defaults = {}
    for module in _loaded_plugins:
        config_key = getattr(module, "CONFIG_KEY", None)
        if config_key:
            defaults[config_key] = bool(getattr(module, "ENABLED_BY_DEFAULT", False))
    return defaults


def available_schemas(config: dict) -> list[dict]:
    schemas = []
    for module in _loaded_plugins:
        config_key = getattr(module, "CONFIG_KEY", None)
        if config_key is not None and not config.get(config_key):
            continue
        schemas.extend(getattr(module, "SCHEMAS", []))
    return schemas


def dispatch_table() -> dict:
    merged = {}
    for module in _loaded_plugins:
        merged.update(getattr(module, "DISPATCH", {}))
    return merged
