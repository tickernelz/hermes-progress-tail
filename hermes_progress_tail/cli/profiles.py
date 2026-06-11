from __future__ import annotations

from pathlib import Path


def _discover_profile_names(hermes_home: Path) -> list[str]:
    profiles_dir = hermes_home / "profiles"
    if not profiles_dir.exists():
        return []
    names = []
    for path in sorted(profiles_dir.iterdir()):
        if not path.is_dir():
            continue
        if (path / "config.yaml").exists() or (path / "plugins").exists():
            names.append(path.name)
    return names


def _resolve_profile_targets(
    hermes_home: Path,
    profiles: list[str] | None = None,
    *,
    all_profiles: bool = False,
) -> list[tuple[str, Path]]:
    hermes_home = Path(hermes_home).expanduser().resolve()
    discovered = _discover_profile_names(hermes_home)
    known = {
        "default": hermes_home,
        **{name: hermes_home / "profiles" / name for name in discovered},
    }
    requested = ["default", *discovered] if all_profiles else (profiles or ["default"])
    targets: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in requested:
        name = str(raw or "").strip()
        if not name:
            continue
        if name in {"base", "main"}:
            name = "default"
        if name not in known:
            available = ", ".join(known) or "default"
            raise ValueError(f"unknown Hermes profile '{name}'. Available profiles: {available}")
        if name in seen:
            continue
        seen.add(name)
        targets.append((name, known[name]))
    return targets or [("default", hermes_home)]


def _parse_profile_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    profiles: list[str] = []
    for value in values:
        profiles.extend(item.strip() for item in value.split(",") if item.strip())
    return profiles or None
