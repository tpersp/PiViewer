#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
weathericonmap.py

Contains:
1) ALL_WEATHER_ICONS: A comprehensive dictionary mapping every Weather Icons
   CSS class (wi-* style) to its corresponding \ufXXXX glyph.
   This includes day/night variants, moon phases, wind directions, etc.

2) OWM_ICON_MAP: A numeric-code mapping for OpenWeatherMap condition IDs -> glyphs.
   (You can expand or adapt it for night logic, or handle 'icon' strings like '01d'.)

3) FALLBACK_ICON: A single fallback glyph (the "na" icon) for unknown conditions.
"""

FALLBACK_ICON = "\uf07b"  # "na" icon from Weather Icons (unknown)

# A more complete ID -> icon mapping.  If you want “night” versions on 800 etc. at nighttime,
# you can adapt or detect "day" vs. "night" from OWM. For now, we just map everything to day icons
# or use fallback for anything not explicitly listed.
OWM_ICON_MAP = {
    # Thunderstorm
    200: "\uf010",  # day-thunderstorm
    201: "\uf010",
    202: "\uf010",
    210: "\uf005",  # day-lightning
    211: "\uf016",  # thunderstorm
    212: "\uf016",
    221: "\uf016",
    230: "\uf010",
    231: "\uf010",
    232: "\uf010",

    # Drizzle
    300: "\uf009",  # day-sprinkle
    301: "\uf009",
    302: "\uf009",
    310: "\uf009",
    311: "\uf009",
    312: "\uf009",
    313: "\uf009",
    314: "\uf009",
    321: "\uf009",

    # Rain
    500: "\uf009",  # day-sprinkle / light-rain
    501: "\uf008",  # day-rain
    502: "\uf008",  # day-rain
    503: "\uf008",
    504: "\uf008",
    511: "\uf006",  # day-rain-mix / freezing-rain
    520: "\uf009",
    521: "\uf009",
    522: "\uf009",
    531: "\uf00e",  # day-storm-showers

    # Snow
    600: "\uf00a",  # day-snow
    601: "\uf00a",  # day-snow
    602: "\uf00a",  # day-snow
    611: "\uf0b2",  # day-sleet
    612: "\uf0b2",
    613: "\uf0b2",
    615: "\uf0b2",
    616: "\uf0b2",
    620: "\uf00a",
    621: "\uf00a",
    622: "\uf00a",

    # Atmosphere
    701: "\uf003",  # day-fog
    711: "\uf062",  # smoke
    721: "\uf0b6",  # day-haze
    731: "\uf063",  # dust
    741: "\uf003",  # day-fog
    751: "\uf063",  # sand
    761: "\uf063",  # dust
    762: "\uf063",  # volcanic ash
    771: "\uf085",  # day-windy or squalls
    781: "\uf056",  # tornado

    # Clear / Clouds
    800: "\uf00d",  # day-sunny
    801: "\uf00c",  # day-cloudy
    802: "\uf002",  # day-cloudy
    803: "\uf013",  # day-cloudy
    804: "\uf013",  # day-cloudy

    # For codes beyond 804, or any not above, we fallback:
}


################################################################################
# ALL_WEATHER_ICONS: Every single Weather Icons CSS class => its \ufXXXX glyph.
#
# These are drawn from the official weather-icons.css, covering:
#   - day- / night- variants
#   - directionals
#   - moon phases
#   - windy, storm, etc.
#   - additional symbols: barometer, sunrise, etc.
################################################################################
ALL_WEATHER_ICONS = {
    "wi-day-sunny": "\uf00d",
    "wi-day-cloudy": "\uf002",
    "wi-day-cloudy-gusts": "\uf000",
    "wi-day-cloudy-windy": "\uf001",
    "wi-day-fog": "\uf003",
    "wi-day-hail": "\uf004",
    "wi-day-haze": "\uf0b6",
    "wi-day-lightning": "\uf005",
    "wi-day-rain": "\uf008",
    "wi-day-rain-mix": "\uf006",
    "wi-day-rain-wind": "\uf007",
    "wi-day-showers": "\uf009",
    "wi-day-sleet": "\uf0b2",
    "wi-day-sleet-storm": "\uf068",
    "wi-day-snow": "\uf00a",
    "wi-day-snow-thunderstorm": "\uf06b",
    "wi-day-snow-wind": "\uf065",
    "wi-day-sprinkle": "\uf00b",
    "wi-day-storm-showers": "\uf00e",
    "wi-day-sunny-overcast": "\uf00c",
    "wi-day-thunderstorm": "\uf010",
    "wi-day-windy": "\uf085",

    "wi-night-clear": "\uf02e",
    "wi-night-alt-cloudy": "\uf086",
    "wi-night-alt-cloudy-gusts": "\uf022",
    "wi-night-alt-cloudy-windy": "\uf023",
    "wi-night-alt-hail": "\uf024",
    "wi-night-alt-lightning": "\uf025",
    "wi-night-alt-rain": "\uf028",
    "wi-night-alt-rain-mix": "\uf026",
    "wi-night-alt-rain-wind": "\uf027",
    "wi-night-alt-showers": "\uf029",
    "wi-night-alt-sleet": "\uf0b4",
    "wi-night-alt-sleet-storm": "\uf06a",
    "wi-night-alt-snow": "\uf02a",
    "wi-night-alt-snow-thunderstorm": "\uf06d",
    "wi-night-alt-sprinkle": "\uf02b",
    "wi-night-alt-storm-showers": "\uf02c",
    "wi-night-alt-thunderstorm": "\uf02d",
    "wi-night-cloudy": "\uf031",
    "wi-night-cloudy-gusts": "\uf02f",
    "wi-night-cloudy-windy": "\uf030",
    "wi-night-fog": "\uf04a",
    "wi-night-hail": "\uf032",
    "wi-night-lightning": "\uf033",
    "wi-night-partly-cloudy": "\uf083",
    "wi-night-rain": "\uf036",
    "wi-night-rain-mix": "\uf034",
    "wi-night-rain-wind": "\uf035",
    "wi-night-showers": "\uf037",
    "wi-night-sleet": "\uf0b3",
    "wi-night-sleet-storm": "\uf069",
    "wi-night-snow": "\uf038",
    "wi-night-snow-thunderstorm": "\uf06c",
    "wi-night-sprinkle": "\uf039",
    "wi-night-storm-showers": "\uf03a",
    "wi-night-thunderstorm": "\uf03b",

    "wi-cloud": "\uf041",
    "wi-cloud-up": "\uf09b",
    "wi-cloud-down": "\uf03d",
    "wi-cloud-refresh": "\uf03c",

    "wi-cloudy": "\uf013",
    "wi-cloudy-gusts": "\uf011",
    "wi-cloudy-windy": "\uf012",
    "wi-fog": "\uf014",
    "wi-hail": "\uf015",
    "wi-rain": "\uf019",
    "wi-rain-mix": "\uf017",
    "wi-rain-wind": "\uf018",
    "wi-showers": "\uf01a",
    "wi-sleet": "\uf0b5",
    "wi-snow": "\uf01b",
    "wi-sprinkle": "\uf01c",
    "wi-storm-showers": "\uf01d",
    "wi-thunderstorm": "\uf01e",

    "wi-snow-wind": "\uf064",
    "wi-smog": "\uf074",
    "wi-smoke": "\uf062",
    "wi-lightning": "\uf016",
    "wi-raindrops": "\uf04e",
    "wi-raindrop": "\uf078",
    "wi-dust": "\uf063",
    "wi-snowflake-cold": "\uf076",
    "wi-windy": "\uf021",
    "wi-strong-wind": "\uf050",

    "wi-hurricane": "\uf073",
    "wi-tornado": "\uf056",
    "wi-small-craft-advisory": "\uf0cc",
    "wi-gale-warning": "\uf0cd",
    "wi-storm-warning": "\uf0ce",
    "wi-hurricane-warning": "\uf0cf",

    "wi-meteor": "\uf071",
    "wi-tsunami": "\uf0c5",
    "wi-earthquake": "\uf0c6",
    "wi-fire": "\uf0c7",
    "wi-volcano": "\uf0c8",
    "wi-flood": "\uf07c",
    "wi-arcus": "\uf0c9",
    "wi-alien": "\uf075",

    "wi-sandstorm": "\uf082",
    "wi-dustwind": "\uf0c3",
    "wi-tornado-warning": "\uf0c4",

    "wi-barometer": "\uf079",
    "wi-humidity": "\uf07a",
    "wi-na": "\uf07b",
    "wi-train": "\uf0cb",

    "wi-moonrise": "\uf0c9",
    "wi-moonset": "\uf0ca",

    # Moon phases (wi-moon-*):
    "wi-moon-new": "\uf095",
    "wi-moon-waxing-crescent-1": "\uf096",
    "wi-moon-waxing-crescent-2": "\uf097",
    "wi-moon-waxing-crescent-3": "\uf098",
    "wi-moon-waxing-crescent-4": "\uf099",
    "wi-moon-waxing-crescent-5": "\uf09a",
    "wi-moon-waxing-crescent-6": "\uf09b",
    "wi-moon-first-quarter": "\uf09c",
    "wi-moon-waxing-gibbous-1": "\uf09d",
    "wi-moon-waxing-gibbous-2": "\uf09e",
    "wi-moon-waxing-gibbous-3": "\uf09f",
    "wi-moon-waxing-gibbous-4": "\uf0a0",
    "wi-moon-waxing-gibbous-5": "\uf0a1",
    "wi-moon-waxing-gibbous-6": "\uf0a2",
    "wi-moon-full": "\uf0a3",
    "wi-moon-waning-gibbous-1": "\uf0a4",
    "wi-moon-waning-gibbous-2": "\uf0a5",
    "wi-moon-waning-gibbous-3": "\uf0a6",
    "wi-moon-waning-gibbous-4": "\uf0a7",
    "wi-moon-waning-gibbous-5": "\uf0a8",
    "wi-moon-waning-gibbous-6": "\uf0a9",
    "wi-moon-third-quarter": "\uf0aa",
    "wi-moon-waning-crescent-1": "\uf0ab",
    "wi-moon-waning-crescent-2": "\uf0ac",
    "wi-moon-waning-crescent-3": "\uf0ad",
    "wi-moon-waning-crescent-4": "\uf0ae",
    "wi-moon-waning-crescent-5": "\uf0af",
    "wi-moon-waning-crescent-6": "\uf0b0",

    "wi-moon-alt-new": "\uf0eb",
    "wi-moon-alt-waxing-crescent-1": "\uf0d0",
    "wi-moon-alt-waxing-crescent-2": "\uf0d1",
    "wi-moon-alt-waxing-crescent-3": "\uf0d2",
    "wi-moon-alt-waxing-crescent-4": "\uf0d3",
    "wi-moon-alt-waxing-crescent-5": "\uf0d4",
    "wi-moon-alt-waxing-crescent-6": "\uf0d5",
    "wi-moon-alt-first-quarter": "\uf0d6",
    "wi-moon-alt-waxing-gibbous-1": "\uf0d7",
    "wi-moon-alt-waxing-gibbous-2": "\uf0d8",
    "wi-moon-alt-waxing-gibbous-3": "\uf0d9",
    "wi-moon-alt-waxing-gibbous-4": "\uf0da",
    "wi-moon-alt-waxing-gibbous-5": "\uf0db",
    "wi-moon-alt-waxing-gibbous-6": "\uf0dc",
    "wi-moon-alt-full": "\uf0dd",
    "wi-moon-alt-waning-gibbous-1": "\uf0de",
    "wi-moon-alt-waning-gibbous-2": "\uf0df",
    "wi-moon-alt-waning-gibbous-3": "\uf0e0",
    "wi-moon-alt-waning-gibbous-4": "\uf0e1",
    "wi-moon-alt-waning-gibbous-5": "\uf0e2",
    "wi-moon-alt-waning-gibbous-6": "\uf0e3",
    "wi-moon-alt-third-quarter": "\uf0e4",
    "wi-moon-alt-waning-crescent-1": "\uf0e5",
    "wi-moon-alt-waning-crescent-2": "\uf0e6",
    "wi-moon-alt-waning-crescent-3": "\uf0e7",
    "wi-moon-alt-waning-crescent-4": "\uf0e8",
    "wi-moon-alt-waning-crescent-5": "\uf0e9",
    "wi-moon-alt-waning-crescent-6": "\uf0ea",

    # More phenomena
    "wi-solar-eclipse": "\uf06e",
    "wi-lunar-eclipse": "\uf070",
    "wi-stars": "\uf077",
    "wi-starry-night": "\uf0cd",
    "wi-sunrise": "\uf051",
    "wi-sunset": "\uf052",
    "wi-sunrise-sea": "\uf0c3",
    "wi-sunset-sea": "\uf0c4",
    "wi-umbrella": "\uf084",
    "wi-raindrop": "\uf078",
    "wi-hot": "\uf072",
    "wi-sunny": "\uf00d",  # same as day-sunny

    # Wind directions & degrees
    "wi-wind-default": "\uf0b7",
    "wi-wind-towards-0-deg": "\uf0b7",
    "wi-wind-towards-90-deg": "\uf0b8",
    "wi-wind-towards-180-deg": "\uf0b9",
    "wi-wind-towards-270-deg": "\uf0ba",
    # Alternatively: "wi-direction-down", "wi-direction-down-left", etc.
    "wi-wind-beaufort-0": "\uf0b7",
    "wi-wind-beaufort-1": "\uf0b8",
    "wi-wind-beaufort-2": "\uf0b9",
    "wi-wind-beaufort-3": "\uf0ba",
    "wi-wind-beaufort-4": "\uf0bb",
    "wi-wind-beaufort-5": "\uf0bc",
    "wi-wind-beaufort-6": "\uf0bd",
    "wi-wind-beaufort-7": "\uf0be",
    "wi-wind-beaufort-8": "\uf0bf",
    "wi-wind-beaufort-9": "\uf0c0",
    "wi-wind-beaufort-10": "\uf0c1",
    "wi-wind-beaufort-11": "\uf0c2",
    "wi-wind-beaufort-12": "\uf0c3",

    # Misc
    "wi-direction-up": "\uf058",
    "wi-direction-down": "\uf044",
    "wi-direction-left": "\uf048",
    "wi-direction-right": "\uf057",
    "wi-compass": "\uf045",
    "wi-thermometer": "\uf055",
    "wi-thermometer-exterior": "\uf053",
    "wi-thermometer-internal": "\uf054",
    "wi-sunrise-over-mountains": "\uf0db",
    "wi-sunset-over-mountains": "\uf0dc",
    "wi-rainbow": "\uf0c0",
    "wi-earth": "\uf057",
    "wi-lightsolar": "\uf0cf"
    # ... certain custom or alternate icons from older versions
}
