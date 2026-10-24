import os
import sys
import hashlib
from pathlib import Path
import re
import unicodedata

import requests
from icalendar import Calendar, Event


OUTPUT_FILE = Path("familie-agenda.ics")

REQUEST_TIMEOUT = 45

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FamilieCalendarMerger/1.0; "
        "+https://github.com/bram-svg)"
    )
}


# ============================================================
# Prefixes
# ============================================================

PERSON_PREFIXES = {
    "niels": "🔴 N - ",
    "fleur": "🟣 F - ",
    "guus": "🔵 G - ",
    "luuk": "🟢 L - ",
    "charlotte": "🟠 C - ",
    "bram": "🟡 B - ",
}

# Icoon voor verjaardagen (voeg dit vóór het onderwerp)
BIRTHDAY_ICON = "🎂 "
# Icoon voor feest/feestelijke onderwerpen (voeg dit als prefix vóór het onderwerp)
PARTY_ICON = "🎉 "
# Medische / afspraak iconen
DENTIST_ICON = "🦷 "
DOCTOR_ICON = "🩺 "
ORTHO_ICON = "🦷 "
# Hockey icoon
HOCKEY_ICON = "🏑 "
# Ski / wintersport icoon
SKI_ICON = "⛷️ "
# Vliegtuig icoon
PLANE_ICON = "✈️ "


# ============================================================
# Bronnen
#
# Gevoelige/private URLs worden uit GitHub Secrets opgehaald.
# Openbare hockey- en Meerlanden-bestanden kunnen rechtstreeks
# in het script staan.
# ============================================================

SOURCES = [
    {
        "name": "Klender familieagenda",
        "url": os.environ.get("KLENDER_ICS_URL", ""),
        "mode": "attendee",
        "prefix": None,
    },
    {
        "name": "Google-agenda Bram",
        "url": os.environ.get("GOOGLE_ICS_URL", ""),
        "mode": "fixed",
        "prefix": "🟡 B - ",
    },
    {
        "name": "Hockey Charlotte",
        "url": "https://bram-svg.github.io/hockey-calendar/team-C.ics",
        "mode": "fixed",
        "prefix": "🟠 C - ",
    },
    {
        "name": "Hockey Fleur",
        "url": "https://bram-svg.github.io/hockey-calendar/team-F.ics",
        "mode": "fixed",
        "prefix": "🟣 F - ",
    },
    {
        "name": "Hockey Guus",
        "url": "https://bram-svg.github.io/hockey-calendar/team-G.ics",
        "mode": "fixed",
        "prefix": "🔵 G - 🟢 L -",
    },
    {
        "name": "Hockey Luuk",
        "url": "https://bram-svg.github.io/hockey-calendar/team-L.ics",
        "mode": "fixed",
        "prefix": "🟢 L - ",
    },
    {
        "name": "Hockey Niels",
        "url": "https://bram-svg.github.io/hockey-calendar/team-N.ics",
        "mode": "fixed",
        "prefix": "🔴 N - ",
    },
    {
        "name": "Meerlanden afvalkalender",
        "url": "https://bram-svg.github.io/meerlanden-calendar/meerlanden.ics",
        "mode": "none",
        "prefix": None,
    },
]


def validate_sources():
    """Controleer of alle benodigde URL's aanwezig zijn."""

    missing = []

    for source in SOURCES:
        if not source["url"]:
            missing.append(source["name"])

    if missing:
        print("De volgende agenda-URL's ontbreken:")
        for name in missing:
            print(f"  - {name}")

        print("\nControleer de GitHub Secrets:")
        print("  - KLENDER_ICS_URL")
        print("  - GOOGLE_ICS_URL")

        sys.exit(1)


def download_calendar(source):
    """Download en parse één ICS-bestand."""

    name = source["name"]
    url = source["url"]

    print(f"Downloaden: {name}")

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Downloaden van '{name}' is mislukt: {error}"
        ) from error

    content = response.content

    if not content or b"BEGIN:VCALENDAR" not in content.upper():
        raise RuntimeError(
            f"De bron '{name}' bevat geen geldig VCALENDAR-bestand."
        )

    try:
        calendar = Calendar.from_ical(content)
    except Exception as error:
        raise RuntimeError(
            f"Het ICS-bestand van '{name}' kon niet worden gelezen: {error}"
        ) from error

    return calendar


def get_attendee_text(event):
    """
    Verzamel tekst uit ATTENDEE en eventueel ATTENDEES.

    Hierbij worden zowel het adres als parameters zoals CN meegenomen.
    Bijvoorbeeld:
        ATTENDEE;CN=Niels:mailto:...
    """

    values = []

    for property_name in ("ATTENDEE", "ATTENDEES"):
        attendee_properties = event.get(property_name)

        if attendee_properties is None:
            continue

        if not isinstance(attendee_properties, list):
            attendee_properties = [attendee_properties]

        for attendee in attendee_properties:
            values.append(str(attendee))

            params = getattr(attendee, "params", {})

            for key, value in params.items():
                values.append(str(key))
                values.append(str(value))

    return " ".join(values).lower()


def determine_attendee_prefix(event):
    """
    Bepaal de prefix op basis van de namen in het ATTENDEE(S)-veld.

    Als meerdere gezinsleden bij dezelfde afspraak staan, worden
    meerdere codes toegevoegd.
    """

    attendee_text = get_attendee_text(event)
    prefixes = []

    for person_name, prefix in PERSON_PREFIXES.items():
        if person_name in attendee_text:
            prefixes.append(prefix.strip())

    if not prefixes:
        return ""

    return " ".join(prefixes) + " "


def get_summary(event):
    """Lees het bestaande onderwerp uit."""

    summary = event.get("SUMMARY")

    if summary is None:
        return "(geen onderwerp)"

    return str(summary)


def already_has_prefix(summary):
    """
    Voorkom dat een prefix bij iedere uitvoering opnieuw wordt toegevoegd.
    """

    known_prefixes = [
        "🔴 N - ",
        "🟣 F - ",
        "🔵 G - ",
        "🟢 L - ",
        "🟠 C - ",
        "🟡 B - ",
    ]

    return any(summary.startswith(prefix) for prefix in known_prefixes)


def has_birthday_icon(summary):
    """Controleer of het verjaardagsicoon of het woord 'verjaardag' al in het onderwerp staat."""

    if not summary:
        return False

    return "🎂" in summary or "verjaardag" in summary.lower()


def has_party_icon(summary):
    """Controleer of het feesticoon al in het onderwerp staat."""

    if not summary:
        return False

    # Alleen controleren op het emoji-teken — niet op het woord 'feest'.
    return "🎉" in summary


def has_medical_icon(summary):
    """Controleer of er al een medisch/tandarts-icoon in het onderwerp staat."""

    if not summary:
        return False

    # Controleer alleen op de daadwerkelijke emoji's, niet op keywords —
    # keywords worden gebruikt om te bepalen of we het icoon moeten toevoegen.
    return "🩺" in summary or "🦷" in summary


def has_hockey_icon(summary):
    """Controleer of het hockey-icoon al in het onderwerp staat."""

    if not summary:
        return False

    # Alleen controleren op het emoji-teken — niet op het woord 'hockey'.
    return "🏑" in summary


def has_ski_icon(summary):
    """Controleer of het ski-icoon al in het onderwerp staat."""

    if not summary:
        return False

    # Alleen controleren op het emoji-teken — niet op woorden zoals 'ski' of 'skiles'.
    return "⛷️" in summary or "⛷" in summary


def has_plane_icon(summary):
    """Controleer of het vliegtuig-icoon al in het onderwerp staat."""

    if not summary:
        return False

    # Sommige bronnen gebruiken de ✈ zonder variation selector, dus check op beide vormen
    return "✈️" in summary or "✈" in summary


def is_yearly_recurring(event):
    """Detecteer of een VEVENT jaarlijks terugkeert (FREQ=YEARLY in RRULE)."""

    rrule = event.get("RRULE")

    if not rrule:
        return False

    try:
        # icalendar's RRULE kan als vRecur of dict-achtig object terugkomen
        if hasattr(rrule, "items"):
            freq = rrule.get("FREQ") or rrule.get("freq")
            if freq:
                if isinstance(freq, (list, tuple)):
                    freq_val = str(freq[0]).lower()
                else:
                    freq_val = str(freq).lower()

                if "year" in freq_val:
                    return True

        # fallback: zoek in de tekstrepresentatie
        txt = str(rrule).lower()
        if "freq=yearly" in txt or "freq=year" in txt:
            return True
    except Exception:
        pass

    return False


def update_event_summary(event, source):
    """Pas het onderwerp aan volgens de configuratie van de bron.

    Extra: voor jaarlijkse afspraken uit de Klender-familieagenda
    wordt het verjaardagsicoon vóór het onderwerp gezet (voor de
    persoons-prefix). Als in het onderwerp 'verjaardag' of 'feest'
    voorkomt, wordt er een feest-icoon vóór het onderwerp geplaatst.
    Voor medische onderwerpen (tandarts/dokter/ortho) wordt het bijpassende
    medisch icoon vóór het onderwerp geplaatst.
    Voor hockey-afspraken (hockey-bron of 'hockey' in onderwerp) wordt het
    hockey-icoon vóór het onderwerp geplaatst.
    Voor ski-/wintersport-afspraken (ski of wintersport in onderwerp) wordt
    het ski-icoon vóór het onderwerp geplaatst.
    Voor vluchten/treinreizen met 'vlucht' in het onderwerp wordt het
    vliegtuig-icoon vóór het onderwerp geplaatst.
    """

    mode = source["mode"]

    if mode == "none":
        return

    original_summary = get_summary(event)
    # normalize to catch unicode variants and unify spacing
    lower_summary = unicodedata.normalize("NFKC", original_summary).lower()
    source_name = (source.get("name") or "").lower()
    source_url = (source.get("url") or "").lower()

    # Bepaal medische iconen
    add_dentist = ("tand" in lower_summary or "tandarts" in lower_summary) and not has_medical_icon(original_summary)
    add_doctor = ("dokter" in lower_summary or "huisarts" in lower_summary or "arts" in lower_summary) and not has_medical_icon(original_summary)
    add_ortho = ("orthodont" in lower_summary or "ortho" in lower_summary) and not has_medical_icon(original_summary)

    medical_prefix = ""
    if add_doctor:
        medical_prefix = DOCTOR_ICON
    elif add_ortho:
        medical_prefix = ORTHO_ICON
    elif add_dentist:
        medical_prefix = DENTIST_ICON

    # Hockey-prefix voor hockey-bronnen en voor Klender-afspraken met 'hockey' in het onderwerp
    add_hockey = (
        ("hockey" in source_name or "hockey" in source_url)
        or bool(re.search(r"\b(hockey|hockey training|training hockey)\b", lower_summary))
    ) and not has_hockey_icon(original_summary)
    hockey_prefix = HOCKEY_ICON if add_hockey else ""

    # Ski-prefix: match diverse vormen zoals 'skiles', 'ski les', 'ski-les'
    add_ski = bool(
        re.search(
            r"(?<!\w)(skiles|ski(?:[\s-]?les)?|wintersport|snowboard|langlaufen)(?!\w)",
            lower_summary,
        )
    ) and not has_ski_icon(original_summary)
    ski_prefix = SKI_ICON if add_ski else ""

    # Vliegtuig-prefix voor onderwerpen met 'vlucht' (of 'vluchten')
    add_plane = bool(re.search(r"\b(vlucht|vluchten)\b", lower_summary)) and not has_plane_icon(original_summary)
    plane_prefix = PLANE_ICON if add_plane else ""

    # Bepaal of we een verjaardagsicoon moeten toevoegen
    add_birthday = (
        source.get("name") == "Klender familieagenda"
        and is_yearly_recurring(event)
        and not has_birthday_icon(original_summary)
    )

    # Bepaal of we een feesticoon moeten toevoegen (als 'verjaardag' of 'feest' in onderwerp)
    add_party = (
        ("verjaardag" in lower_summary or "feest" in lower_summary)
        and not has_party_icon(original_summary)
    )

    # Zoek de prefix die we normaal zouden toevoegen
    prefix_to_add = ""
    if not already_has_prefix(original_summary):
        if mode == "fixed":
            prefix_to_add = source["prefix"] or ""
        elif mode == "attendee":
            prefix_to_add = determine_attendee_prefix(event)
        else:
            raise ValueError(
                f"Onbekende verwerkingsmodus voor '{source['name']}': {mode}"
            )

    # Bouw het onderwerp: volgorde van iconen/prefixes is:
    # MEDICAL_ICON + HOCKEY_ICON + SKI_ICON + PLANE_ICON + PARTY_ICON + BIRTHDAY_ICON + prefix + originele onderwerp
    new_summary = original_summary

    if already_has_prefix(original_summary):
        # Als er al een prefix is, voeg iconen vóór het bestaande onderwerp
        if medical_prefix:
            new_summary = f"{medical_prefix}{new_summary}"
        if hockey_prefix:
            new_summary = f"{hockey_prefix}{new_summary}"
        if ski_prefix:
            new_summary = f"{ski_prefix}{new_summary}"
        if plane_prefix:
            new_summary = f"{plane_prefix}{new_summary}"
        if add_birthday:
            new_summary = f"{BIRTHDAY_ICON}{new_summary}"
        if add_party:
            new_summary = f"{PARTY_ICON}{new_summary}"
    else:
        # Voeg prefix (indien aanwezig) vóór onderwerp
        if prefix_to_add:
            new_summary = f"{prefix_to_add}{new_summary}"
        # Voeg iconen vóór alles (medisch, hockey, ski en vliegtuig eerst)
        if medical_prefix:
            new_summary = f"{medical_prefix}{new_summary}"
        if hockey_prefix:
            new_summary = f"{hockey_prefix}{new_summary}"
        if ski_prefix:
            new_summary = f"{ski_prefix}{new_summary}"
        if plane_prefix:
            new_summary = f"{plane_prefix}{new_summary}"
        if add_birthday:
            new_summary = f"{BIRTHDAY_ICON}{new_summary}"
        if add_party:
            new_summary = f"{PARTY_ICON}{new_summary}"

    # Schrijf het nieuwe onderwerp indien gewijzigd
    if new_summary != original_summary:
        event["SUMMARY"] = new_summary


def component_signature(component):
    """
    Maak een sleutel waarmee exact dubbele onderdelen worden voorkomen.

    Voor afspraken worden UID en RECURRENCE-ID gebruikt. Voor onderdelen
    zonder UID wordt een hash van de volledige component gemaakt.
    """

    component_name = getattr(component, "name", "").upper()

    if component_name == "VEVENT":
        uid = str(component.get("UID", "")).strip()
        recurrence_id = str(component.get("RECURRENCE-ID", "")).strip()

        if uid:
            return f"VEVENT|{uid}|{recurrence_id}"

    raw_component = component.to_ical()
    digest = hashlib.sha256(raw_component).hexdigest()

    return f"{component_name}|{digest}"


def build_output_calendar():
    """Download, verwerk en combineer alle agenda's."""

    validate_sources()

    output_calendar = Calendar()

    output_calendar.add("PRODID", "-//Familie van der Heijden//Gecombineerde kalender//NL")
    output_calendar.add("VERSION", "2.0")
    output_calendar.add("CALSCALE", "GREGORIAN")
    output_calendar.add("X-WR-CALNAME", "Familie Van der Heijden")
    output_calendar.add("X-WR-CALDESC", "Gecombineerde familieagenda")

    added_components = set()
    total_events = 0

    for source in SOURCES:
        calendar = download_calendar(source)

        source_events = 0

        for component in calendar.subcomponents:
            component_name = getattr(component, "name", "").upper()

            # Alleen relevante kalendercomponenten overnemen.
            if component_name not in {
                "VEVENT",
                "VTODO",
                "VJOURNAL",
                "VTIMEZONE",
            }:
                continue

            if component_name == "VEVENT":
                update_event_summary(component, source)

            signature = component_signature(component)

            if signature in added_components:
                print(
                    f"  Dubbel onderdeel overgeslagen in "
                    f"'{source['name']}': {signature}"
                )
                continue

            added_components.add(signature)
            output_calendar.add_component(component)

            if component_name == "VEVENT":
                source_events += 1
                total_events += 1

        print(f"  Toegevoegd: {source_events} afspraken")

    if total_events == 0:
        raise RuntimeError(
            "Er zijn geen afspraken gevonden. "
            "Het bestaande uitvoerbestand wordt daarom niet vervangen."
        )

    temporary_file = OUTPUT_FILE.with_suffix(".ics.tmp")

    with temporary_file.open("wb") as file:
        file.write(output_calendar.to_ical())

    temporary_file.replace(OUTPUT_FILE)

    print()
    print(f"Gereed: {OUTPUT_FILE}")
    print(f"Totaal aantal afspraken: {total_events}")


def main():
    try:
        build_output_calendar()
    except Exception as error:
        print()
        print(f"FOUT: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
