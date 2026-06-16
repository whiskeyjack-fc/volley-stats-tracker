#This project will call a website and gets an xml file and parse it.
from datetime import datetime, timedelta
import requests
from xml.etree import ElementTree
import json
import logging
import sys
import colorama
from colorama import Fore, Style
import os
from datetime import datetime

colorama.init(autoreset=True)


def resource_path(relative_path: str) -> str:
    """Resolve resource path for dev runs and PyInstaller onefile."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, relative_path)

if "--debug" in sys.argv:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO, format='%(message)s')

# fetch_xml function to retrieve XML data from a URL
def fetch_xml(url):
    """Fetch XML data from a given URL."""
    logging.debug(f"XML-data ophalen van {url}...")
    try:
        response = requests.get(url)
        response.raise_for_status()
        logging.debug(f"Succesvol data opgehaald van {url}")
        logging.debug(f"Respons statuscode: {response.status_code}")
        return response.content
    except requests.RequestException as e:
        logging.error(f"Fout bij het ophalen van data: {e}")
        return None

# load_trainers_data function to load trainers data from a JSON file
def load_trainers_data(json_file='trainers.json'):
    """Laad trainersdata uit een JSON-bestand."""
    logging.debug(f"Trainersdata laden uit {json_file}...")
    try:
        json_path = json_file if os.path.isabs(json_file) else resource_path(json_file)
        with open(json_path, 'r', encoding='utf-8') as f:
            trainers_data = json.load(f)
        logging.debug(f"{len(trainers_data)} trainers geladen uit {json_file}.")
        logging.debug(f"Trainersdata succesvol geladen uit {json_file}.")
        return trainers_data
    except Exception as e:
        logging.error(f"Fout bij het laden van trainersdata: {e}")
        return []

# parse_xml function to parse the XML data and return a list of wedstrijden
def parse_xml(xml_data):
    """Parse XML-data en retourneer een lijst van wedstrijden."""
    logging.debug("Starten met het parsen van de XML-data...")
    try:
        root = ElementTree.fromstring(xml_data)
        parsed_data = []
        for wedstrijd in root.findall('.//wedstrijd'):
            datum = wedstrijd.findtext('datum', default='')
            aanvangsuur = wedstrijd.findtext('aanvangsuur', default='')
            reeks = wedstrijd.findtext('reeks', default='')
            if reeks.startswith(('OHP', 'ODP', 'OBP')):
                promo = True
            else:
                promo = False
            try:
                if promo:
                    start_dt = datetime.strptime(f"{datum} {aanvangsuur}", "%d/%m/%Y %H:%M") - timedelta(minutes=150)
                else:
                    start_dt = datetime.strptime(f"{datum} {aanvangsuur}", "%d/%m/%Y %H:%M") - timedelta(minutes=60)
                einde_dt = datetime.strptime(f"{datum} {aanvangsuur}", "%d/%m/%Y %H:%M") + timedelta(hours=2)
                start_str = start_dt.strftime("%d/%m/%Y %H:%M")
                einde_str = einde_dt.strftime("%d/%m/%Y %H:%M")
            except ValueError:
                start_str = datum + ' ' + aanvangsuur
                einde_str = ''
            thuisploeg = wedstrijd.findtext('thuisploeg', default='')
            bezoekersploeg = wedstrijd.findtext('bezoekersploeg', default='')
            if 'vc belvoc belsele' in thuisploeg.lower():
                ploeg = thuisploeg
            else:
                ploeg = bezoekersploeg
            data = {
                'datum': datum,
                'aanvangsuur': aanvangsuur,
                'reeks': reeks,
                'thuisploeg': thuisploeg,
                'bezoekersploeg': bezoekersploeg,
                'ploeg': ploeg,
                'sporthal': wedstrijd.findtext('sporthal', default=''),
                'start': start_str,
                'einde': einde_str
            }
            parsed_data.append(data)
        logging.debug(f"Succesvol {len(parsed_data)} wedstrijden geparsed.")
        return parsed_data
    except ElementTree.ParseError as e:
        logging.error(f"Fout bij het parsen van XML: {e}")
        return []


def filter_future_matches(wedstrijden, now=None):
    """Filter wedstrijden: behoud enkel wedstrijden met start >= nu."""
    if now is None:
        now = datetime.now()

    filtered = []
    for w in wedstrijden:
        try:
            start_dt = datetime.strptime(w["start"], "%d/%m/%Y %H:%M")
        except Exception:
            continue

        if start_dt >= now:
            filtered.append(w)

    return filtered

def normalize_ploeg(name):
    """Normalize team name for matching: strip (+) suffix, collapse whitespace, lowercase."""
    return ' '.join(name.replace('(+)', '').split()).lower()


# merge_xml function to merge the XML data with the trainers.json file
def merge_xml(parsed_xml, trainers_data):
    """
    Voeg geparste XML-data samen met trainersdata.
    Voor elke wedstrijd, maak een nieuwe regel voor elke bijpassende trainer (left outer join).
    Als er geen trainer is, voeg een regel toe met lege trainer-velden.
    """
    logging.debug("Starten met het samenvoegen van wedstrijddata met trainersdata...")
    merged = []
    for wedstrijd in parsed_xml:
        matching_trainers = [
            trainer for trainer in trainers_data
            if trainer['reeks'] == wedstrijd['reeks'] and normalize_ploeg(trainer['ploeg']) == normalize_ploeg(wedstrijd['ploeg'])
        ]
        if matching_trainers:
            for trainer in matching_trainers:
                merged.append({**wedstrijd, **trainer})
        else:
            merged.append({
                **wedstrijd,
                "naam": None,
                "type": None
            })
            print(f"Geen bijpassende trainer gevonden voor wedstrijd: {wedstrijd['ploeg']} in reeks: {wedstrijd['reeks']}")
    logging.debug(f"Succesvol {len(merged)} wedstrijdregels samengevoegd met trainersdata.")
    return merged

# function to calculate the overlap duration between two time intervals
def overlap_duration(start1, end1, start2, end2):
    overlap_start = max(start1, start2)
    overlap_end = min(end1, end2)
    if overlap_start < overlap_end:
        # Calculate the duration of the overlap in minutes
        duration = (overlap_end - overlap_start).total_seconds() / 60
        # calculate the percentage of overlap
        percentage_overlap = (duration / ((end2 - start2).total_seconds() / 60)) * 100
        # return the percentage overlap
        return f"{duration:.0f} minuten ({percentage_overlap:.0f}%)"
    else:
        return None  # No overlap


# function to detect Sporthal conflicts
# for all wedstrijden in the sporthal "Belsele, Sporthal De Klavers" detect if there are any matches that overlap in time
def detect_sporthal_conflicts(wedstrijden):
    """Detecteer groepen van wedstrijden met meer dan 2 overlappende tijdsloten in dezelfde sporthal."""
    logging.debug("Detecteren van sporthalconflicten voor Belsele, Sporthal De Klavers...")
    sporthal_name = "Belsele, Sporthal De Klavers"
    sporthal_wedstrijden = [w for w in wedstrijden if w['sporthal'] == sporthal_name]

    # Convert start and end to datetime for comparison
    for w in sporthal_wedstrijden:
        w['start_dt'] = datetime.strptime(w['start'], "%d/%m/%Y %H:%M")
        w['einde_dt'] = datetime.strptime(w['einde'], "%d/%m/%Y %H:%M")

    def overlaps_in_time(a, b):
        # Treat touching boundaries as overlap (keeps current behavior)
        return a['start_dt'] <= b['einde_dt'] and a['einde_dt'] >= b['start_dt']

    # Build overlap clusters per day (connected components)
    overlaps = []
    wedstrijden_per_dag = {}
    for w in sporthal_wedstrijden:
        wedstrijden_per_dag.setdefault(w['start_dt'].date(), []).append(w)

    for _, day_matches in wedstrijden_per_dag.items():
        day_matches = sorted(day_matches, key=lambda x: (x['start_dt'], x.get('reeks', ''), x.get('bezoekersploeg', '')))
        n = len(day_matches)
        if n < 3:
            continue

        adjacency = [set() for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                if overlaps_in_time(day_matches[i], day_matches[j]):
                    adjacency[i].add(j)
                    adjacency[j].add(i)

        visited = [False] * n
        for i in range(n):
            if visited[i]:
                continue
            stack = [i]
            visited[i] = True
            component_indices = []
            while stack:
                cur = stack.pop()
                component_indices.append(cur)
                for nb in adjacency[cur]:
                    if not visited[nb]:
                        visited[nb] = True
                        stack.append(nb)

            if len(component_indices) > 2:
                group = [day_matches[idx] for idx in component_indices]
                group = sorted(group, key=lambda x: (x['start_dt'], x.get('reeks', ''), x.get('bezoekersploeg', '')))

                # Provide a stable per-match overlap indicator: max overlap with any other match in the group
                for m in group:
                    best = None
                    best_minutes = -1
                    for other in group:
                        if other is m:
                            continue
                        od = overlap_duration(other['start_dt'], other['einde_dt'], m['start_dt'], m['einde_dt'])
                        if od:
                            minutes = int(od.split(' ', 1)[0])
                            if minutes > best_minutes:
                                best_minutes = minutes
                                best = od
                    m['overlap_duration'] = best

                overlaps.append(group)
    # sort overlaps by start time
    overlaps = sorted(overlaps, key=lambda x: x[0]['start_dt'])
    # Clean up temporary fields
    for w in sporthal_wedstrijden:
        w.pop('start_dt', None)
        w.pop('einde_dt', None)

    # report the successful detection of conflicts
    logging.debug("Detectie van sporthalconflicten afgerond")
    # for debugging purposes, print the overlaps found
    #print( overlaps)
    return overlaps

# function to detect groups of conflicting games per trainer
def detect_trainer_conflicts(merged_data):
    """Detecteer conflicten voor elke trainer op basis van hun wedstrijden, en sla groepen overlappende wedstrijden op."""
    logging.debug("Detecteren van trainerconflicten...")
    trainer_games = {}
    trainer_conflict_groups = {}

    for entry in merged_data:
        trainer_name = entry.get('naam')
        if not trainer_name:
            continue  # Skip entries without a trainer

        if trainer_name not in trainer_games:
            trainer_games[trainer_name] = []
            trainer_conflict_groups[trainer_name] = []

        # Convert start and end to datetime for comparison
        start_dt = datetime.strptime(entry['start'], "%d/%m/%Y %H:%M")
        einde_dt = datetime.strptime(entry['einde'], "%d/%m/%Y %H:%M")

        # Add the entry to the trainer's games
        trainer_games[trainer_name].append({
            **entry,
            'start_dt': start_dt,
            'einde_dt': einde_dt
        })
    # Sort the games for each trainer by start time, reeks and bezoekersploeg
    for trainer_name in trainer_games:
        trainer_games[trainer_name] = sorted(trainer_games[trainer_name], key=lambda x: (x['start_dt'], x['reeks'], x['bezoekersploeg']))

    # Now, for each trainer, find groups of overlapping matches
    for trainer_name, games in trainer_games.items():
        games = sorted(games, key=lambda x: x['start_dt'])
        n = len(games)

        for i in range(n):

            group = [games[i]]

            for j in range(n):
                if i != j and games[i]['start_dt'].date() == games[j]['start_dt'].date():
                    if (games[j]['start_dt'] < group[-1]['einde_dt'] and
                        games[j]['einde_dt'] > group[-1]['start_dt']):
                        # calculate the overlap duration
                        overlap = overlap_duration(
                            games[i]['start_dt'],
                            games[i]['einde_dt'],
                            games[j]['start_dt'],
                            games[j]['einde_dt']
                        )
                        #add the overlap duration to the match
                        games[j]['overlap_duration'] = overlap

                        group.append(games[j])

            if len(group) > 1:
                # Sort group by start, reeks and bezoekersploeg for consistency
                group = sorted(group, key=lambda x: (x['start_dt'], x['reeks'], x['bezoekersploeg']))
                # check if the group already exists in the conflict groups
                if group not in trainer_conflict_groups[trainer_name]:
                    # Store the group as a conflict group
                    trainer_conflict_groups[trainer_name].append(group)

    logging.debug("Detectie van trainerconflicten afgerond")
    return trainer_conflict_groups

def get_log_filename(base_dir=".", prefix="belvoc_log"):
    today = datetime.now().strftime("%Y%m%d")
    seq = 1
    while True:
        filename = os.path.join(base_dir, f"{prefix}_{today}_{seq}.txt")
        if not os.path.exists(filename):
            return filename
        seq += 1

# main function to orchestrate the fetching and parsing of XML data
def main():
    """Hoofdfunctie om conflictdetectie voor Belvoc-wedstrijden uit te voeren."""
    # report the start of the main function
    logging.debug("Starten met het detecteren van conflicten voor Belvoc-wedstrijden...")
    # URL for the XML data of matches for Belvoc
    url = "http://www.volleyadmin2.be/services/wedstrijden_xml.php?stamnummer=O-2186"  # list of matches for Belvoc
    xml_data = fetch_xml(url)
    
    # Prepare log file
    log_filename = get_log_filename()
    log_lines = []

    # Replace logging.info(...) with both console and file output
    def log_info(msg):
        logging.info(msg)
        log_lines.append(msg)

    if xml_data:
        wedstrijd_data = parse_xml(xml_data)
        wedstrijd_data = filter_future_matches(wedstrijd_data)
        # Detect conflicts in sporthal schedules
        sporthal_conflicts = detect_sporthal_conflicts(wedstrijd_data)
        if sporthal_conflicts:
            log_info(Fore.YELLOW + "=" * 80)
            log_info("Sporthalconflicten gedetecteerd:")
            log_info(Fore.YELLOW + "=" * 80)
            for conflict in sporthal_conflicts:
                log_info(f"Conflicten op {conflict[0]['datum']}:")
                for sporthal_match in conflict:
                    log_info(f"  - {sporthal_match['reeks']} => {sporthal_match['thuisploeg']} vs {sporthal_match['bezoekersploeg']} om {sporthal_match['start']} (duur overlap: {sporthal_match.get('overlap_duration', 'N/B')})")
            log_info(Fore.YELLOW + "-" * 80)
            log_info(f"Totaal aantal sporthalconflicten gedetecteerd: {len(sporthal_conflicts)}")
        else:
            log_info("Geen sporthalconflicten gevonden in het sporthalrooster.")
        trainers_data = load_trainers_data()
        merged_data = merge_xml(wedstrijd_data, trainers_data)
        # report the number of merged items
        logging.debug(f"Samengevoegde data bevat {len(merged_data)} items.")
        # Detect conflicts per trainer
        trainer_conflict_groups = detect_trainer_conflicts(merged_data)
        if trainer_conflict_groups:
            # Print each trainer's conflict groups
            log_info(Fore.YELLOW + "=" * 80)
            log_info("Trainerconflicten gedetecteerd:")
            log_info(Fore.YELLOW + "=" * 80)
            for trainer, groups in trainer_conflict_groups.items():
                # Print trainer name if there are conflicts
                if not groups:
                    continue
                log_info(Fore.YELLOW + "-" * 80)
                log_info(Fore.CYAN + f"{trainer}")
                log_info(Fore.YELLOW + "-" * 80)
                for group in groups:
                    log_info(f"Conflicten op {group[0]['datum']}:")
                    for trainer_match in group:
                        log_info(f"    - {trainer_match['reeks']} => {trainer_match['thuisploeg']} vs {trainer_match['bezoekersploeg']} om {trainer_match['start']} in {trainer_match['sporthal']} (duur overlap: {trainer_match.get('overlap_duration', 'N/B')})")
        else:
            log_info("Geen trainerconflicten gevonden.")
        log_info(Fore.YELLOW + "=" * 80)

    # Write log to file (strip color codes for file)
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    with open(log_filename, "w", encoding="utf-8") as f:
        for line in log_lines:
            f.write(ansi_escape.sub('', line) + "\n")

    logging.debug(f"Logbestand opgeslagen als: {log_filename}")
    # report the end of the main function
    logging.debug("Detectieproces voor Belvoc-wedstrijden afgerond.")

if __name__ == "__main__":
    main()

