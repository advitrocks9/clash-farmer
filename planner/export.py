from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

_mapping: dict[str, str] | None = None


def _get_mapping() -> dict[str, str]:
    global _mapping
    if _mapping is None:
        raw = json.loads((DATA_DIR / "mapping.json").read_text())
        _mapping = {k: v for k, v in raw.items() if v}
    return _mapping


@dataclass
class Building:
    id: int
    name: str
    level: int
    count: int = 1
    timer: int | None = None  # seconds remaining if upgrading
    weapon: int | None = None  # TH weapon level
    gear_up: bool = False

    @property
    def upgrading(self) -> bool:
        return self.timer is not None


@dataclass
class Hero:
    id: int
    name: str
    level: int
    timer: int | None = None

    @property
    def upgrading(self) -> bool:
        return self.timer is not None


@dataclass
class BaseState:
    tag: str
    timestamp: int
    th_level: int
    buildings: list[Building]
    traps: list[Building]
    heroes: list[Hero]

    @property
    def free_builders(self) -> int:
        total = sum(
            b.count for b in self.buildings
            if b.name in ("Builders_Hut", "B.O.B_Hut")
        )
        upgrading = sum(
            1 for b in self.buildings if b.upgrading
        ) + sum(
            1 for h in self.heroes if h.upgrading
        )
        return max(0, total - upgrading)

    @property
    def upgrading_buildings(self) -> list[Building]:
        return [b for b in self.buildings if b.upgrading]

    @property
    def upgrading_heroes(self) -> list[Hero]:
        return [h for h in self.heroes if h.upgrading]

    def buildings_by_name(self, name: str) -> list[Building]:
        return [b for b in self.buildings if b.name == name]

    def hero_by_name(self, name: str) -> Hero | None:
        for h in self.heroes:
            if h.name == name:
                return h
        return None


def parse_export(raw_json: str) -> BaseState:
    data = json.loads(raw_json)
    mapping = _get_mapping()

    tag = data.get("tag", "")
    timestamp = data.get("timestamp", 0)

    buildings: list[Building] = []
    th_level = 1
    for entry in data.get("buildings", []):
        data_id = entry.get("data")
        name = mapping.get(str(data_id), "")
        if not name:
            continue
        level = entry.get("lvl", 0)
        count = entry.get("cnt", 1)
        timer = entry.get("timer")
        weapon = entry.get("weapon")
        gear_up = bool(entry.get("gear_up"))

        if name == "Town_Hall":
            th_level = level
            if weapon is not None:
                buildings.append(Building(data_id, name, level, 1, timer, weapon, gear_up))
            continue

        if timer is not None:
            buildings.append(Building(data_id, name, level, 1, timer, weapon, gear_up))
            if count and count > 1:
                buildings.append(Building(data_id, name, level, count - 1, None, weapon, gear_up))
        else:
            buildings.append(Building(data_id, name, level, count, None, weapon, gear_up))

    traps: list[Building] = []
    for entry in data.get("traps", []):
        data_id = entry.get("data")
        name = mapping.get(str(data_id), "")
        if not name:
            continue
        traps.append(Building(
            data_id, name, entry.get("lvl", 0), entry.get("cnt", 1), entry.get("timer"),
        ))

    heroes: list[Hero] = []
    for entry in data.get("heroes", []):
        data_id = entry.get("data")
        name = mapping.get(str(data_id), "")
        if not name:
            continue
        heroes.append(Hero(data_id, name, entry.get("lvl", 0), entry.get("timer")))

    return BaseState(
        tag=tag,
        timestamp=timestamp,
        th_level=th_level,
        buildings=buildings,
        traps=traps,
        heroes=heroes,
    )


def load_upgrade_data(category: str) -> dict:
    path = DATA_DIR / f"{category}.json"
    return json.loads(path.read_text())


def get_max_level(building_name: str, th_level: int) -> int | None:
    for category in ("defenses", "resources", "army", "traps"):
        data = load_upgrade_data(category)
        if building_name in data:
            levels = [e for e in data[building_name] if e.get("TH", 0) <= th_level]
            if levels:
                return max(e["level"] for e in levels)
    return None


def get_upgrade_info(building_name: str, target_level: int) -> dict | None:
    for category in ("defenses", "resources", "army", "traps"):
        data = load_upgrade_data(category)
        if building_name in data:
            for entry in data[building_name]:
                if entry["level"] == target_level:
                    return entry
    return None


def get_hero_max_level(hero_name: str, hero_hall_level: int) -> int | None:
    data = load_upgrade_data("heroes")
    if hero_name not in data:
        return None
    levels = [e for e in data[hero_name] if e.get("HH", 0) <= hero_hall_level]
    if levels:
        return max(e["level"] for e in levels)
    return None


def state_to_planner_json(state: BaseState, resources: dict[str, int]) -> dict:
    return {
        "tag": state.tag,
        "th_level": state.th_level,
        "free_builders": state.free_builders,
        "resources": resources,
        "buildings": [
            {
                "name": b.name,
                "level": b.level,
                "count": b.count,
                "upgrading": b.upgrading,
                "max_level": get_max_level(b.name, state.th_level),
            }
            for b in state.buildings
            if b.name != "Wall"
        ],
        "heroes": [
            {
                "name": h.name,
                "level": h.level,
                "upgrading": h.upgrading,
            }
            for h in state.heroes
        ],
        "traps": [
            {
                "name": t.name,
                "level": t.level,
                "count": t.count,
                "upgrading": t.upgrading,
            }
            for t in state.traps
        ],
    }
