import streamlit as st
import requests
from bs4 import BeautifulSoup
from graphviz import Digraph
from datetime import datetime
import urllib.parse
import textwrap
import re
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape

# --- API KONFIGURATION ---
USER_AGENT = "WikiMetrik/1.0 (Kontakt: mein_email@domain.com)"

# Unterstützte Sprachen
LANGUAGES = {
    "Deutsch (de)": "de",
    "English (en)": "en",
    "Français (fr)": "fr",
    "Español (es)": "es",
    "Italiano (it)": "it",
    "Nederlands (nl)": "nl",
    "Polski (pl)": "pl"
}

def get_lang_from_url(url):
    try:
        return urllib.parse.urlparse(url).netloc.split('.')[0]
    except Exception:
        return 'de'

# --- INTELLIGENTE SUCHE & VORSCHLÄGE ---
def fetch_search_suggestions(query, lang="de"):
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 35,
        "namespace": 0,
        "format": "json"
    }
    headers = {"User-Agent": USER_AGENT}
    
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            results = []
            if len(data) >= 4:
                for i in range(len(data[1])):
                    results.append({"titel": data[1][i], "url": data[3][i]})
            return results
    except Exception:
        pass
    return []

def lade_kategorie_mitglieder(kategorie_name, lang="de", fortsetzung=None, anzahl=50):
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT}
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": kategorie_name if kategorie_name.startswith("Category:") or kategorie_name.startswith("Kategorie:") else f"Category:{kategorie_name}",
        "cmtype": "page",
        "cmlimit": str(anzahl),
        "format": "json",
    }
    if fortsetzung:
        params["cmcontinue"] = fortsetzung

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return [], None, f"Status-Code {response.status_code}"

        data = response.json()
        mitglieder = data.get("query", {}).get("categorymembers", [])
        artikel = [
            {
                "titel": m["title"],
                "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(m["title"].replace(" ", "_"))
            }
            for m in mitglieder
        ]
        naechster_token = data.get("continue", {}).get("cmcontinue")
        return artikel, naechster_token, None

    except Exception as e:
        return [], None, f"Fehler beim Laden der Kategorie: {str(e)}"

# --- FEATURE-EXTRAKTOREN ---
def extrahiere_bilder(soup, base_url):
    bilder = []
    gesehene_urls = set()

    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src')
        if not src:
            continue

        if src.startswith('//'):
            src = 'https:' + src
        elif src.startswith('/'):
            src = base_url + src

        breite = img.get('width')
        try:
            breite_px = int(breite) if breite else None
        except ValueError:
            breite_px = None

        ist_icon = breite_px is not None and breite_px <= 20
        ist_svg = src.lower().endswith('.svg')

        if ist_icon or src in gesehene_urls:
            continue

        caption = ""
        figure_eltern = img.find_parent('figure')
        if figure_eltern:
            figcaption = figure_eltern.find('figcaption')
            if figcaption:
                caption = figcaption.get_text(separator=' ', strip=True)
        if not caption:
            thumb_eltern = img.find_parent(class_='thumbinner')
            if thumb_eltern:
                caption_div = thumb_eltern.find(class_='thumbcaption')
                if caption_div:
                    caption = caption_div.get_text(separator=' ', strip=True)

        link = ""
        a_eltern = img.find_parent('a')
        if a_eltern and a_eltern.get('href'):
            href = a_eltern['href']
            if href.startswith('//'):
                link = 'https:' + href
            elif href.startswith('/'):
                link = base_url + href
            else:
                link = href

        if not ist_svg:
            bilder.append({"src": src, "link": link, "caption": caption})
            gesehene_urls.add(src)

    return bilder[:24]

def extrahiere_zeitleiste(text):
    zeitleiste = []
    text_clean = re.sub(r'\[\d+\]', '', text)
    saetze = re.split(r'(?<=[.!?]) +', text_clean)

    mengen_woerter = (
        r'(Seite|Seiten|Mitarbeiter|Mitarbeitern|Mitarbeiterinnen|Exemplar|Exemplare|Exemplaren|'
        r'Euro|Dollar|Mark|Pfund|Einwohner|Einwohnern|Menschen|Personen|Soldaten|'
        r'Meter|Kilometer|km|Stück|Teilnehmer|Teilnehmern|Punkte|Punkten|'
        r'Mal|Male|Tonnen|Kilogramm|kg|Quadratkilometer|km²|Häftlinge|Häftlingen|'
        r'Stimmen|Sitze|Sitzen|Worte|Wörter|Zeichen|Mio\.?|Millionen|Milliarden)'
    )

    for satz in saetze:
        if not (30 < len(satz) < 300):
            continue

        for match in re.finditer(r'\b(1[0-9]{3}|20[0-9]{2})\b', satz):
            jahr = int(match.group(1))
            start, ende = match.span()

            vor_kontext = satz[max(0, start - 15):start]
            nach_kontext = satz[ende:ende + 20]

            if re.match(r'\s*' + mengen_woerter + r'\b', nach_kontext, re.IGNORECASE):
                continue
            if re.match(r'^[.,]\d', nach_kontext) or re.search(r'[.,]$', vor_kontext):
                continue
            if re.match(r'\s*[%€$]', nach_kontext):
                continue
            if re.match(r'\s*[-–]\s*\d', nach_kontext):
                continue
            if re.match(r'^\d', nach_kontext) or re.search(r'\d$', vor_kontext):
                continue

            zeitleiste.append((jahr, satz.strip()))
            break  

    zeitleiste.sort(key=lambda x: x[0])

    gefiltert = []
    gesehene_saetze = set()
    for jahr, satz in zeitleiste:
        if satz not in gesehene_saetze:
            gefiltert.append({"jahr": jahr, "text": satz})
            gesehene_saetze.add(satz)

    return gefiltert

def get_traversal_start(headline_elem):
    parent = headline_elem.parent
    if parent and parent.name == "div" and parent.get("class") and \
       any("mw-heading" in c for c in parent.get("class")):
        return parent
    return headline_elem

def wrap_fuer_mindmap(text, breite=20, max_zeilen=4):
    zeilen = textwrap.wrap(text, width=breite) or [text]
    if len(zeilen) > max_zeilen:
        zeilen = zeilen[:max_zeilen]
        zeilen[-1] = zeilen[-1].rstrip() + " …"
    return '\n'.join(zeilen)

def extrahiere_infobox(soup):
    infobox = soup.find('table', class_='infobox')
    if not infobox:
        return {}

    tbody = infobox.find('tbody', recursive=False) or infobox
    daten = {}
    letzter_key = None

    for row in tbody.find_all('tr', recursive=False):
        th = row.find('th', recursive=False)
        alle_tds = row.find_all('td', recursive=False)

        if th and alle_tds:
            key = th.get_text(separator=' ', strip=True)
            val = alle_tds[0].get_text(separator=' ', strip=True)
            if key and val:
                daten[key] = val
                letzter_key = key
        elif not th and len(alle_tds) >= 2:
            label_td = row.find('td', class_='infobox-label', recursive=False) or alle_tds[0]
            daten_td = row.find('td', class_='infobox-data', recursive=False) or alle_tds[1]
            key = label_td.get_text(separator=' ', strip=True)
            val = daten_td.get_text(separator=' ', strip=True)
            if key and val:
                daten[key] = val
                letzter_key = key
        elif not th and len(alle_tds) == 1 and letzter_key:
            zusatz = alle_tds[0].get_text(separator=' ', strip=True)
            if zusatz:
                daten[letzter_key] = f"{daten[letzter_key]} {zusatz}".strip()
    return daten

def extrahiere_siehe_auch(alle_headlinetags, base_url):
    ergebnisse = []
    gesehene_texte = set()
    for i, headline in enumerate(alle_headlinetags):
        h_text = headline.get_text(strip=True).lower()
        if "siehe auch" in h_text or "see also" in h_text or "voir aussi" in h_text or "véase también" in h_text:
            start = get_traversal_start(headline)
            naechste = alle_headlinetags[i + 1] if i + 1 < len(alle_headlinetags) else None
            stop = get_traversal_start(naechste) if naechste else None
            el = start.find_next_sibling()
            while el and el != stop:
                for a in el.find_all('a'):
                    text = a.get_text(strip=True)
                    href = a.get('href', '')
                    if text and href.startswith('/wiki/') and ':' not in href.split('/wiki/')[-1]:
                        if text not in gesehene_texte:
                            voll_url = base_url + href
                            ergebnisse.append({"text": text, "url": voll_url})
                            gesehene_texte.add(text)
                el = el.find_next_sibling()
    return ergebnisse

# --- HAUPT API LOGIK ---
def scrape_wikipedia_advanced(url):
    headers = {"User-Agent": USER_AGENT}
    try:
        lang = get_lang_from_url(url)
        api_url = f"https://{lang}.wikipedia.org/w/api.php"
        base_url = f"https://{lang}.wikipedia.org"
        
        titel_raw = urllib.parse.unquote(url.split("/wiki/")[-1])
        
        params = {
            "action": "parse",
            "page": titel_raw,
            "prop": "text|sections|categories|langlinks",
            "format": "json",
            "redirects": 1
        }
        
        response = requests.get(api_url, params=params, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None, f"Fehler: Status-Code {response.status_code}"
            
        data = response.json()
        if "error" in data:
             return None, f"Wikipedia API Fehler: {data['error'].get('info', 'Unbekannter Fehler')}"
             
        parse_data = data["parse"]
        titel = parse_data["title"]
        
        html_content = parse_data["text"]["*"]
        soup = BeautifulSoup(html_content, 'html.parser')
        
        absaetze = soup.find_all('p')
        gesamter_text = "\n\n".join([p.text.strip() for p in absaetze if p.text.strip()])
        
        ignorierte_sektionen = ["Einzelnachweise", "Literatur", "Weblinks", "Siehe auch", "Inhaltsverzeichnis", "Anmerkungen", "Weblinks und Quellen", "References", "See also", "External links", "Notes"]
        struktur = []
        aktuelle_h2 = None
        
        for sec in parse_data.get("sections", []):
            ebene = sec["toclevel"]
            text = sec["line"]
            if any(ign in text for ign in ignorierte_sektionen) or not text:
                continue
                
            if ebene == 1:
                aktuelle_h2 = text
                struktur.append({"typ": "h2", "text": text, "kinder": []})
            elif ebene == 2 and aktuelle_h2 and struktur:
                struktur[-1]["kinder"].append(text)

        quellen_daten = {"Einzelnachweise": [], "Literatur": [], "Weblinks": []}
        alle_headlinetags = soup.find_all(['h2', 'h3'])
        
        for i, headline in enumerate(alle_headlinetags):
            headline_span = headline.find(class_="mw-headline")
            h_text = headline_span.text.strip().lower() if headline_span else headline.text.strip().lower()
            
            schluessel = None
            if "einzelnachweis" in h_text or "anmerkung" in h_text or "referen" in h_text or "note" in h_text:
                schluessel = "Einzelnachweise"
            elif "literatur" in h_text or "bibliograph" in h_text or "further reading" in h_text:
                schluessel = "Literatur"
            elif "weblink" in h_text or "internetquelle" in h_text or "external link" in h_text:
                schluessel = "Weblinks"
                
            if schluessel:
                start_element = get_traversal_start(headline)
                naechste_headline_roh = alle_headlinetags[i+1] if i+1 < len(alle_headlinetags) else None
                stop_element = get_traversal_start(naechste_headline_roh) if naechste_headline_roh else None
                
                aktuelles_element = start_element.find_next_sibling()
                while aktuelles_element and aktuelles_element != stop_element:
                    listen_eintraege = aktuelles_element.find_all('li')
                    if not listen_eintraege and aktuelles_element.name in ['div', 'ol', 'ul']:
                        listen_eintraege = aktuelles_element.find_all('li')
                        
                    for li in listen_eintraege:
                        text = li.text.strip()
                        text = text.replace('↑ ', '').strip()

                        links_in_eintrag = []
                        for a in li.find_all('a', href=True):
                            href = a['href']
                            if href.startswith('http://') or href.startswith('https://'):
                                if href not in links_in_eintrag:
                                    links_in_eintrag.append(href)
                            elif href.startswith('//'):
                                voll = 'https:' + href
                                if voll not in links_in_eintrag:
                                    links_in_eintrag.append(voll)
                            elif href.startswith('/'):
                                voll = base_url + href
                                if voll not in links_in_eintrag:
                                    links_in_eintrag.append(voll)

                        if text:
                            bereits_vorhanden = any(eintrag["text"] == text for eintrag in quellen_daten[schluessel])
                            if not bereits_vorhanden:
                                quellen_daten[schluessel].append({"text": text, "links": links_in_eintrag})
                    aktuelles_element = aktuelles_element.find_next_sibling()

        infobox_daten = extrahiere_infobox(soup)
        infobox_roh_gefunden = soup.find('table', class_='infobox') is not None
        
        kategorien = [cat["*"] for cat in parse_data.get("categories", []) if "hidden" not in cat]
        sprachlinks = {lang_obj["lang"]: lang_obj["url"] for lang_obj in parse_data.get("langlinks", [])}
        
        siehe_auch = extrahiere_siehe_auch(alle_headlinetags, base_url)
        bilder_urls = extrahiere_bilder(soup, base_url)
        zeitleiste = extrahiere_zeitleiste(gesamter_text)

        return {
            "titel": titel, 
            "text": gesamter_text, 
            "struktur": struktur,
            "quellen": quellen_daten,
            "infobox": infobox_daten,
            "infobox_roh_gefunden": infobox_roh_gefunden,
            "kategorien": kategorien,
            "siehe_auch": siehe_auch,
            "sprachlinks": sprachlinks,
            "bilder": bilder_urls,
            "zeitleiste": zeitleiste,
            "url": url,
            "lang": lang
        }, None
        
    except Exception as e:
        return None, f"Ein Fehler ist aufgetreten: {str(e)}"

def scrape_sprachversion_kompakt(url):
    headers = {"User-Agent": USER_AGENT}
    try:
        titel_raw = urllib.parse.unquote(url.split("/wiki/")[-1])
        base_api_url = "https://" + url.split("/")[2] + "/w/api.php"
        
        params = {
            "action": "parse",
            "page": titel_raw,
            "prop": "text|sections",
            "format": "json",
            "redirects": 1
        }
        
        response = requests.get(base_api_url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, f"Status-Code {response.status_code}"

        data = response.json()
        if "error" in data:
             return None, "Sprachversion konnte nicht geladen werden."
             
        parse_data = data["parse"]
        titel = parse_data["title"]
        html_content = parse_data["text"]["*"]
        soup = BeautifulSoup(html_content, 'html.parser')

        absaetze = soup.find_all('p')
        gesamter_text = "\n\n".join([p.text.strip() for p in absaetze if p.text.strip()])

        ueberschriften = [{"ebene": "h2", "text": sec["line"]} for sec in parse_data.get("sections", []) if sec["toclevel"] == 1]

        anzahl_quellen = 0
        ref_container = soup.find_all(class_=["reflist", "references"])
        oberste_container = [
            c for c in ref_container
            if not any(c in anderer.find_all(class_=["reflist", "references"]) for anderer in ref_container if anderer is not c)
        ]
        for container in oberste_container:
            anzahl_quellen += len(container.find_all('li'))

        return {
            "titel": titel,
            "wortanzahl": len(gesamter_text.split()),
            "anzahl_abschnitte": len(ueberschriften),
            "ueberschriften": ueberschriften,
            "anzahl_quellen": anzahl_quellen
        }, None

    except Exception as e:
        return None, f"Fehler beim Abruf: {str(e)}"

def generiere_zitation(titel, url, zitierstil="Harvard"):
    heute = datetime.now()
    monate_de = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
    abrufdatum_de_kurz = heute.strftime("%d.%m.%Y")
    abrufdatum_de = f"{heute.day}. {monate_de[heute.month - 1]} {heute.year}"
    jahr = heute.year

    formate = {
        "Harvard": f"Wikipedia ({jahr}) '{titel}'. Verfügbar unter: {url} (Abgerufen: {abrufdatum_de_kurz}).",
        "APA 7": f"Wikipedia-Autoren. ({jahr}, {monate_de[heute.month - 1]} {heute.day}). *{titel}*. In Wikipedia. Abgerufen am {abrufdatum_de}, von {url}",
        "MLA 9": f'"{titel}." *Wikipedia*, Wikimedia Foundation, {abrufdatum_de}, {url}.',
        "IEEE": f"Wikipedia-Autoren, \"{titel},\" *Wikipedia*, {jahr}. [Online]. Verfügbar: {url}. [Zugriff: {abrufdatum_de_kurz}].",
        "Kurzform (Inline)": f"({titel}, Wikipedia {jahr})",
    }
    return formate.get(zitierstil, formate["Harvard"])

# --- PDF-REPORT-EXPORT ---
def erstelle_pdf_report(daten, zitierstil="Harvard"):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

    def esc(text):
        return xml_escape(str(text))

    puffer = BytesIO()
    doc = SimpleDocTemplate(
        puffer, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
        title=daten['titel'], author="WikiMetrik"
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='WikiLensTitel', parent=styles['Title'], fontSize=22, spaceAfter=4))
    styles.add(ParagraphStyle(name='WikiLensMeta', parent=styles['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=16))
    styles.add(ParagraphStyle(name='WikiLensH2', parent=styles['Heading2'], spaceBefore=16, spaceAfter=8, textColor=colors.HexColor('#1a1a2e')))
    styles.add(ParagraphStyle(name='WikiLensKlein', parent=styles['Normal'], fontSize=9, leading=12))
    body_style = styles['Normal']
    body_style.fontSize = 10
    body_style.leading = 14

    story = []

    story.append(Paragraph(esc(daten['titel']), styles['WikiLensTitel']))
    heute_str = datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")
    story.append(Paragraph(
        f"WikiMetrik-Analysereport &middot; Quelle: "
        f"<link href='{esc(daten['url'])}' color='blue'>{esc(daten['url'])}</link> "
        f"&middot; erstellt am {heute_str}",
        styles['WikiLensMeta']
    ))
    story.append(HRFlowable(width="100%", color=colors.HexColor('#cccccc'), thickness=1))
    story.append(Spacer(1, 12))

    if daten.get('infobox'):
        story.append(Paragraph("Infobox", styles['WikiLensH2']))
        tabellen_daten = [[Paragraph(f"<b>{esc(k)}</b>", styles['WikiLensKlein']),
                            Paragraph(esc(v), styles['WikiLensKlein'])]
                           for k, v in daten['infobox'].items()]
        tabelle = Table(tabellen_daten, colWidths=[4.5*cm, 11*cm])
        tabelle.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f2f2f2')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(tabelle)
        story.append(Spacer(1, 8))

    story.append(Paragraph("Zitierfähige Angabe", styles['WikiLensH2']))
    zitat = generiere_zitation(daten['titel'], daten['url'], zitierstil=zitierstil)
    story.append(Paragraph(esc(zitat), body_style))

    doc.build(story)
    puffer.seek(0)
    return puffer.getvalue()

def erstelle_text_pdf(titel, text, url):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    def esc(text_str):
        return xml_escape(str(text_str))

    puffer = BytesIO()
    doc = SimpleDocTemplate(
        puffer, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm, topMargin=2.5*cm, bottomMargin=2.5*cm,
        title=titel, author="WikiMetrik"
    )

    styles = getSampleStyleSheet()
    titel_style = ParagraphStyle(name='ReaderTitel', parent=styles['Title'], fontSize=20, spaceAfter=12)
    body_style = ParagraphStyle(name='ReaderBody', parent=styles['Normal'], fontSize=11, leading=16, spaceAfter=10, alignment=0)
    meta_style = ParagraphStyle(name='ReaderMeta', parent=styles['Normal'], fontSize=9, textColor=colors.grey, spaceAfter=16)

    story = []
    story.append(Paragraph(esc(titel), titel_style))
    story.append(Paragraph(f"Quelle: <link href='{esc(url)}' color='blue'>{esc(url)}</link>", meta_style))
    story.append(HRFlowable(width="100%", color=colors.HexColor('#cccccc'), thickness=1))
    story.append(Spacer(1, 12))

    for absatz in text.split('\n\n'):
        if absatz.strip():
            story.append(Paragraph(esc(absatz.strip()), body_style))

    doc.build(story)
    puffer.seek(0)
    return puffer.getvalue()


st.set_page_config(page_title="WikiMetrik", page_icon="🧠", layout="wide")

st.title("🧠 WikiMetrik")
st.markdown("Suche nach einem Thema, wähle eine Sprache oder gib direkt einen Wikipedia-Link ein.")

if "daten" not in st.session_state:
    st.session_state["daten"] = None
if "fehler" not in st.session_state:
    st.session_state["fehler"] = None
if "such_ergebnisse" not in st.session_state:
    st.session_state["such_ergebnisse"] = None

def fuehre_analyse_aus(eingabe_url):
    keys_to_clear = [
        "sprachvergleich_ergebnis", "sprachvergleich_fehler", "sprachvergleich_sprache",
        "pdf_report_bytes", "pdf_report_url", "kategorie_browser_aktiv", "kategorie_browser_daten",
        "such_ergebnisse"
    ]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]

    with st.spinner("Frage offizielle Wikipedia-API an..."):
        daten, fehler = scrape_wikipedia_advanced(eingabe_url)
        st.session_state["daten"] = daten
        st.session_state["fehler"] = fehler

# --- SUCHE UI ---
col_suche1, col_suche2 = st.columns([1, 4])
with col_suche1:
    lang_auswahl = st.selectbox("🌐 Sprache", list(LANGUAGES.keys()), index=0)
    lang_code = LANGUAGES[lang_auswahl]
with col_suche2:
    nutzer_eingabe = st.text_input("Wikipedia URL oder Suchbegriff", placeholder="z. B. 'Albert Einstein' oder URL...")

if st.button("🔍 Suchen / Artikel finden", type="primary"):
    if nutzer_eingabe:
        if nutzer_eingabe.startswith("http"):
            fuehre_analyse_aus(nutzer_eingabe)
            st.rerun()
        else:
            with st.spinner(f"Suche nach '{nutzer_eingabe}'..."):
                ergebnisse = fetch_search_suggestions(nutzer_eingabe, lang_code)
                if not ergebnisse:
                    st.warning("Keine passenden Artikel gefunden. Bitte versuche einen anderen Begriff.")
                    st.session_state["such_ergebnisse"] = None
                else:
                    st.session_state["such_ergebnisse"] = ergebnisse
    else:
        st.warning("Bitte gib einen Suchbegriff oder eine URL ein.")

# Suchergebnis-Auswahl anzeigen
if st.session_state.get("such_ergebnisse"):
    st.markdown("### 🎯 Meintest du einen dieser Artikel?")
    auswahl_titel = st.radio("Wähle den passenden Artikel aus:", [e['titel'] for e in st.session_state["such_ergebnisse"]])
    if st.button("🚀 Artikel analysieren"):
        gewaehlte_url = next(e['url'] for e in st.session_state["such_ergebnisse"] if e['titel'] == auswahl_titel)
        fuehre_analyse_aus(gewaehlte_url)
        st.rerun()

st.divider()

# --- ANALYSE ERGEBNISSE ---
daten = st.session_state["daten"]
fehler = st.session_state["fehler"]

if daten is not None or fehler is not None:
    if fehler:
        st.error(fehler)
    else:
        st.success(f"Analyse abgeschlossen für: **{daten['titel']}**")

        pdf_export_spalte1, pdf_export_spalte2 = st.columns([1, 3])
        with pdf_export_spalte1:
            if st.button("📑 PDF-Report erstellen", key="pdf_erstellen_btn"):
                with st.spinner("Erstelle PDF-Report..."):
                    pdf_stil = st.session_state.get("zitierstil_auswahl", "Harvard")
                    pdf_bytes = erstelle_pdf_report(daten, zitierstil=pdf_stil)
                st.session_state["pdf_report_bytes"] = pdf_bytes
                st.session_state["pdf_report_url"] = daten["url"]

        if (st.session_state.get("pdf_report_bytes") and
                st.session_state.get("pdf_report_url") == daten["url"]):
            with pdf_export_spalte2:
                st.download_button(
                    "💾 Report herunterladen",
                    data=st.session_state["pdf_report_bytes"],
                    file_name=f"{daten['titel'].replace(' ', '_')}_WikiMetrik_Report.pdf",
                    mime="application/pdf",
                    key="pdf_download_btn"
                )

        if st.session_state.get("kategorie_browser_aktiv"):
            aktive_kategorie = st.session_state["kategorie_browser_aktiv"]
            aktuelle_lang = daten["lang"]
            with st.container(border=True):
                kb_kopf1, kb_kopf2 = st.columns([5, 1])
                with kb_kopf1:
                    st.markdown(f"#### 🗂️ Kategorie-Browser: „{aktive_kategorie}“")
                with kb_kopf2:
                    if st.button("✕ Schließen", key="kategorie_browser_schliessen"):
                        st.session_state.pop("kategorie_browser_aktiv", None)
                        st.session_state.pop("kategorie_browser_daten", None)
                        st.rerun()

                if "kategorie_browser_daten" not in st.session_state:
                    with st.spinner(f"Lade Artikel aus „{aktive_kategorie}“..."):
                        artikel, naechster_token, kb_fehler = lade_kategorie_mitglieder(aktive_kategorie, lang=aktuelle_lang)
                    st.session_state["kategorie_browser_daten"] = {
                        "artikel": artikel,
                        "naechster_token": naechster_token,
                        "fehler": kb_fehler,
                    }

                kb_daten = st.session_state["kategorie_browser_daten"]

                if kb_daten["fehler"]:
                    st.error(kb_daten["fehler"])
                elif not kb_daten["artikel"]:
                    st.info("Keine Artikel in dieser Kategorie gefunden.")
                else:
                    st.caption(f"{len(kb_daten['artikel'])} Artikel geladen. Klick auf ➜ analysiert den Artikel direkt.")
                    kb_cols = st.columns(2)
                    for kb_idx, kb_artikel in enumerate(kb_daten["artikel"]):
                        with kb_cols[kb_idx % 2]:
                            kbe_spalte1, kbe_spalte2 = st.columns([5, 1])
                            with kbe_spalte1:
                                st.markdown(f"<a href='{kb_artikel['url']}' target='_blank' style='text-decoration:none;'>🔗 {kb_artikel['titel']}</a>", unsafe_allow_html=True)
                            with kbe_spalte2:
                                if st.button("➜", key=f"kb_sprung_{kb_idx}_{kb_artikel['titel']}"):
                                    fuehre_analyse_aus(kb_artikel['url'])
                                    st.rerun()

                    if kb_daten["naechster_token"]:
                        if st.button("⬇️ Weitere Artikel laden", key="kategorie_browser_mehr"):
                            with st.spinner("Lade weitere Artikel..."):
                                neue_artikel, neuer_token, kb_fehler2 = lade_kategorie_mitglieder(
                                    aktive_kategorie, lang=aktuelle_lang, fortsetzung=kb_daten["naechster_token"]
                                )
                            if kb_fehler2:
                                st.error(kb_fehler2)
                            else:
                                kb_daten["artikel"].extend(neue_artikel)
                                kb_daten["naechster_token"] = neuer_token
                                st.session_state["kategorie_browser_daten"] = kb_daten
                            st.rerun()
                    else:
                        st.caption("✓ Alle Artikel dieser Kategorie sind geladen.")

        tab_mindmap, tab_text, tab_zeitleiste, tab_galerie, tab_quellen, tab_info, tab_sprachen, tab_zitat = st.tabs([
            "🗺️ Mindmap", 
            "📄 Text",
            "⏳ Zeitleiste",
            "🖼️ Galerie",
            "📚 Quellen", 
            "ℹ️ Übersicht",
            "🌍 Sprachen",
            "🖊️ Zitieren"
        ])
            
        with tab_mindmap:
            st.subheader("Automatische Inhalts-Struktur")
            st.markdown("💡 *Tipp: Klicke auf die Knoten, um den entsprechenden Wikipedia-Abschnitt in einem neuen Tab zu öffnen.*")
            if not daten['struktur']:
                st.info("Keine tieferen Überschriften zur Strukturierung gefunden.")
            else:
                dot = Digraph(comment=daten['titel'])
                dot.attr(rankdir='LR') 
                dot.attr(target='_blank')
                
                dot.node('Haupt', wrap_fuer_mindmap(daten['titel'], breite=25), 
                         style='filled', color='lightblue', shape='ellipse', URL=daten['url'], target='_blank')
                    
                for i, h2 in enumerate(daten['struktur']):
                    h2_id = f"h2_{i}"
                    h2_anker = urllib.parse.quote(h2['text'].replace(' ', '_'))
                    h2_url = f"{daten['url']}#{h2_anker}"
                    
                    dot.node(h2_id, wrap_fuer_mindmap(h2['text'], breite=20), 
                             style='filled', color='lightgray', shape='box', URL=h2_url, target='_blank')
                    dot.edge('Haupt', h2_id)
                    
                    for j, h3 in enumerate(h2['kinder']):
                        h3_id = f"h3_{i}_{j}"
                        h3_anker = urllib.parse.quote(h3.replace(' ', '_'))
                        h3_url = f"{daten['url']}#{h3_anker}"
                        
                        dot.node(h3_id, wrap_fuer_mindmap(h3, breite=18), shape='plaintext', URL=h3_url, target='_blank')
                        dot.edge(h2_id, h3_id)
                
                st.graphviz_chart(dot)
                
                st.divider()
                st.markdown("#### 💾 Mindmap exportieren")
                mm_col1, mm_col2, mm_col3 = st.columns(3)
                with mm_col1:
                    st.download_button(label="📝 Als .dot herunterladen", data=dot.source, file_name=f"Mindmap_{daten['titel'].replace(' ', '_')}.dot", mime="text/plain", use_container_width=True)
                try:
                    png_daten = dot.pipe(format='png')
                    with mm_col2:
                        st.download_button(label="🖼️ Als PNG herunterladen", data=png_daten, file_name=f"Mindmap_{daten['titel'].replace(' ', '_')}.png", mime="image/png", use_container_width=True)
                except Exception:
                    with mm_col2: st.error("PNG-Export benötigt Graphviz auf dem Server.")
                try:
                    svg_daten = dot.pipe(format='svg')
                    with mm_col3:
                        st.download_button(label="📊 Als SVG herunterladen", data=svg_daten, file_name=f"Mindmap_{daten['titel'].replace(' ', '_')}.svg", mime="image/svg+xml", use_container_width=True)
                except Exception:
                    with mm_col3: st.error("SVG-Export benötigt Graphviz auf dem Server.")

        with tab_text:
            st.subheader("Lesemodus")
            export_col1, export_col2, _ = st.columns([2, 2, 6])
            with export_col1:
                st.download_button("💾 Als .txt herunterladen", data=daten['text'], file_name=f"{daten['titel'].replace(' ', '_')}.txt", use_container_width=True)
            with export_col2:
                pdf_text_bytes = erstelle_text_pdf(daten['titel'], daten['text'], daten['url'])
                st.download_button("📄 Als .pdf herunterladen", data=pdf_text_bytes, file_name=f"{daten['titel'].replace(' ', '_')}_Text.pdf", mime="application/pdf", use_container_width=True)

            st.divider()
            _, reader_col, _ = st.columns([1, 6, 1])
            with reader_col:
                for absatz in daten['text'].split('\n\n'):
                    if absatz.strip():
                        st.markdown(f"<p style='font-size: 1.15rem; line-height: 1.8; margin-bottom: 1.2rem; text-align: justify;'>{xml_escape(absatz.strip())}</p>", unsafe_allow_html=True)

        with tab_zeitleiste:
            st.subheader("⏳ Chronologischer Überblick")
            if daten['zeitleiste']:
                try:
                    sortierte_events = sorted(daten["zeitleiste"], key=lambda x: int(re.sub(r"\D", "", str(x["jahr"])) or 0))
                except Exception:
                    sortierte_events = daten["zeitleiste"]

                st.markdown("""
                    <style>
                    .timeline-container { border-left: 3px solid var(--primary-color, #FF4B4B); padding-left: 20px; margin-left: 10px; margin-top: 15px; }
                    .timeline-item { position: relative; margin-bottom: 25px; }
                    .timeline-item::before { content: ''; position: absolute; left: -27px; top: 5px; width: 12px; height: 12px; border-radius: 50%; background-color: var(--background-color, #ffffff); border: 3px solid var(--primary-color, #FF4B4B); }
                    .timeline-badge { display: inline-block; background-color: var(--primary-color, #FF4B4B); color: white; padding: 3px 10px; border-radius: 12px; font-weight: bold; font-size: 0.85em; margin-bottom: 5px; }
                    .timeline-card { background-color: var(--secondary-background-color, #f0f2f6); padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
                    </style>
                """, unsafe_allow_html=True)

                st.markdown('<div class="timeline-container">', unsafe_allow_html=True)
                for event in sortierte_events:
                    st.markdown(f"""
                        <div class="timeline-item">
                            <div class="timeline-badge">{xml_escape(str(event.get('jahr', '')))}</div>
                            <div class="timeline-card">{xml_escape(str(event.get('text', '')))}</div>
                        </div>
                    """, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.info("Für diesen Artikel konnten keine historischen Jahreszahlen extrahiert werden.")

        with tab_galerie:
            st.subheader("Artikel-Galerie")
            if not daten['bilder']:
                st.info("Keine Bilder in diesem Artikel gefunden.")
            else:
                cols = st.columns(3)
                for i, bild in enumerate(daten['bilder']):
                    with cols[i % 3]:
                        st.image(bild["src"], use_container_width=True, caption=bild.get("caption") or None)
                        if bild.get("link"):
                            st.markdown(f"<a href='{bild['link']}' target='_blank' style='text-decoration:none;'>🔗 Bildquelle öffnen</a>", unsafe_allow_html=True)

        with tab_quellen:
            st.subheader("Literaturnachweise, Quellen und Weblinks")
            suchbegriff = st.text_input("🔍 Quellen durchsuchen", key="quellen_suche")

            def filtere(liste, begriff):
                if not begriff: return liste
                return [eintrag for eintrag in liste if begriff.lower() in eintrag["text"].lower()]

            def rendere_quelleneintrag(eintrag):
                text = eintrag["text"]
                links = eintrag.get("links", [])
                if links:
                    link_html = " ".join(f"<a href='{url}' target='_blank'>[🔗 Link {i+1}]</a>" for i, url in enumerate(links))
                    st.markdown(f"<li>{text} <br> {link_html}</li>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<li>{text}</li>", unsafe_allow_html=True)

            einzelnachweise_gefiltert = filtere(daten['quellen']['Einzelnachweise'], suchbegriff)
            literatur_gefiltert = filtere(daten['quellen']['Literatur'], suchbegriff)
            weblinks_gefiltert = filtere(daten['quellen']['Weblinks'], suchbegriff)

            spalte1, spalte2, spalte3 = st.columns(3)
                
            with spalte1:
                st.markdown("### 📝 Einzelnachweise")
                if einzelnachweise_gefiltert:
                    st.markdown("<ul>", unsafe_allow_html=True)
                    for ref in einzelnachweise_gefiltert[:40]: rendere_quelleneintrag(ref)
                    st.markdown("</ul>", unsafe_allow_html=True)
                    if len(einzelnachweise_gefiltert) > 40:
                        with st.expander(f"Weitere {len(einzelnachweise_gefiltert)-40} anzeigen"):
                            st.markdown("<ul>", unsafe_allow_html=True)
                            for ref in einzelnachweise_gefiltert[40:]: rendere_quelleneintrag(ref)
                            st.markdown("</ul>", unsafe_allow_html=True)
                else: st.info("Keine direkten Einzelnachweise gefunden.")
                        
            with spalte2:
                st.markdown("### 📖 Literatur")
                if literatur_gefiltert:
                    st.markdown("<ul>", unsafe_allow_html=True)
                    for lit in literatur_gefiltert: rendere_quelleneintrag(lit)
                    st.markdown("</ul>", unsafe_allow_html=True)
                else: st.info("Keine Literatureinträge gefunden.")
                        
            with spalte3:
                st.markdown("### 🔗 Weblinks")
                if weblinks_gefiltert:
                    st.markdown("<ul>", unsafe_allow_html=True)
                    for link in weblinks_gefiltert: rendere_quelleneintrag(link)
                    st.markdown("</ul>", unsafe_allow_html=True)
                else: st.info("Keine externen Weblinks gefunden.")

        with tab_info:
            st.subheader("Schnellübersicht")
            ueb_spalte1, ueb_spalte2 = st.columns([3, 2])
            with ueb_spalte1:
                st.markdown("### 📋 Infobox")
                if daten['infobox']:
                    for key, value in daten['infobox'].items(): st.markdown(f"**{key}:** {value}")
                elif daten.get('infobox_roh_gefunden'):
                    st.warning("Eine Infobox wurde gefunden, aber ihre Struktur konnte nicht ausgelesen werden.")
                else:
                    st.info("Keine Infobox auf dieser Seite gefunden.")

                st.markdown("### 🔗 Siehe auch")
                if daten['siehe_auch']:
                    st.caption("Direkt weiter recherchieren: Klick auf ➜ analysiert den verlinkten Artikel sofort neu.")
                    for idx, eintrag in enumerate(daten['siehe_auch']):
                        sa_spalte1, sa_spalte2 = st.columns([5, 1])
                        with sa_spalte1:
                            st.markdown(f"<a href='{eintrag['url']}' target='_blank' style='text-decoration:none;'>🔗 {eintrag['text']}</a>", unsafe_allow_html=True)
                        with sa_spalte2:
                            if st.button("➜", key=f"siehe_auch_sprung_{idx}", help="Direkt analysieren"):
                                fuehre_analyse_aus(eintrag['url'])
                                st.rerun()
                else:
                    st.info("Kein 'Siehe auch'-Abschnitt gefunden.")

            with ueb_spalte2:
                st.markdown("### 🏷️ Kategorien")
                if daten['kategorien']:
                    for kat_idx, kat in enumerate(daten['kategorien']):
                        if st.button(kat, key=f"kategorie_browser_{kat_idx}"):
                            st.session_state["kategorie_browser_aktiv"] = kat
                            st.session_state.pop("kategorie_browser_daten", None)
                            st.rerun()
                else:
                    st.info("Keine Kategorien gefunden.")

        with tab_sprachen:
            st.subheader("Artikel in anderen Sprachen vergleichen")
            if not daten['sprachlinks']:
                st.info("Keine anderssprachigen Versionen gefunden.")
            else:
                optionen = {f"{code.upper()} ({code})": code for code in daten['sprachlinks'].keys()}
                gewaehlte_anzeige = st.selectbox("Mit Sprachversion vergleichen:", options=list(optionen.keys()))
                    
                if st.button("Vergleichen", key="sprachvergleich_btn"):
                    with st.spinner(f"Lade {gewaehlte_anzeige}..."):
                        vergleich, vfehler = scrape_sprachversion_kompakt(daten['sprachlinks'][optionen[gewaehlte_anzeige]])
                    st.session_state["sprachvergleich_ergebnis"] = vergleich
                    st.session_state["sprachvergleich_fehler"] = vfehler
                    st.session_state["sprachvergleich_sprache"] = gewaehlte_anzeige

                vergleich = st.session_state.get("sprachvergleich_ergebnis")
                vfehler = st.session_state.get("sprachvergleich_fehler")
                if vfehler:
                    st.error(f"Fehler: {vfehler}")
                elif vergleich:
                    ew, eh = len(daten['text'].split()), len(daten['struktur'])
                    eq = len(daten['quellen']['Einzelnachweise']) + len(daten['quellen']['Literatur']) + len(daten['quellen']['Weblinks'])
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Wortanzahl", f"{ew:,}", delta=f"{ew - vergleich['wortanzahl']:,} vs. Original")
                    m2.metric("Abschnitte", eh, delta=eh - vergleich['anzahl_abschnitte'])
                    m3.metric("Quellen", eq, delta=eq - vergleich['anzahl_quellen'])

        with tab_zitat:
            st.subheader("Artikel zitieren")
            zitierstile = ["Harvard", "APA 7", "MLA 9", "IEEE", "Kurzform (Inline)"]
            gewaehlter_stil = st.selectbox("Zitierformat", options=zitierstile, key="zitierstil_auswahl")
            zitat_text = generiere_zitation(daten['titel'], daten['url'], zitierstil=gewaehlter_stil)
            st.text_area("Generierte Zitation", value=zitat_text, height=100)

# --- IMPRESSUM IM FOOTER ---
st.markdown("---")
with st.expander("⚖️ Impressum"):
    st.markdown(
        """
        **Angaben gemäß § 5 TMG:** Kayra Ciftci  
        Wikimetrik.com
        
        c/o flexdienst – #21358  
        Kurt-Schumacher-Straße 76  
        67663 Kaiserslautern  
        Deutschland  
        """
    )