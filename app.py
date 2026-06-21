import streamlit as st
import requests
from bs4 import BeautifulSoup
from graphviz import Digraph
from datetime import datetime
import urllib.parse
import textwrap
import re
import google.generativeai as genai

# --- INTELLIGENTE SUCHE & URL-AUFLÖSUNG ---
def resolve_wikipedia_input(user_input):
    user_input = user_input.strip()
    
    if user_input.startswith("http://") or user_input.startswith("https://"):
        return user_input, None
    
    suche_url = f"https://de.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(user_input)}&limit=1&namespace=0&format=json"
    headers = {"User-Agent": "MeinAdvancedStreamlitBot/1.0 (Kontakt: mein_email@domain.com)"}
    
    try:
        response = requests.get(suche_url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 3 and data[3]:
                gefundene_url = data[3][0]
                return gefundene_url, None
    except Exception:
        pass
        
    return None, "Link oder Begriff konnte nicht gefunden werden"

# --- FEATURE-EXTRAKTOREN (Bilder & Zeitleiste) ---
def extrahiere_bilder(soup):
    bilder = []
    for a in soup.find_all('a', class_='image'):
        img = a.find('img')
        if img:
            src = img.get('src') or img.get('data-src')
            if src:
                if src.startswith('//'):
                    src = 'https:' + src
                if not src.endswith('.svg') and src not in bilder:
                    bilder.append(src)
    return bilder[:24] 

def extrahiere_zeitleiste(text):
    zeitleiste = []
    text_clean = re.sub(r'\[\d+\]', '', text)
    saetze = re.split(r'(?<=[.!?]) +', text_clean)
    
    for satz in saetze:
        match = re.search(r'\b(1[0-9]{3}|20[0-9]{2})\b', satz)
        if match:
            jahr = int(match.group(1))
            if 30 < len(satz) < 300:
                zeitleiste.append((jahr, satz.strip()))
    
    zeitleiste.sort(key=lambda x: x[0])
    
    gefiltert = []
    gesehene_saetze = set()
    for jahr, satz in zeitleiste:
        if satz not in gesehene_saetze:
            gefiltert.append({"jahr": jahr, "text": satz})
            gesehene_saetze.add(satz)
            
    return gefiltert

# --- ERWEITERTE & ULTRAROBUSTE SCRAPER LOGIK ---
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

def extrahiere_kategorien(soup):
    catlinks = soup.find(id="catlinks")
    if not catlinks:
        return []
    return [a.get_text(strip=True) for a in catlinks.find_all('a') if a.get_text(strip=True)]

def extrahiere_siehe_auch(alle_headlinetags):
    ergebnisse = []
    for i, headline in enumerate(alle_headlinetags):
        h_text = headline.get_text(strip=True)
        if "siehe auch" in h_text.lower():
            start = get_traversal_start(headline)
            naechste = alle_headlinetags[i + 1] if i + 1 < len(alle_headlinetags) else None
            stop = get_traversal_start(naechste) if naechste else None
            el = start.find_next_sibling()
            while el and el != stop:
                for a in el.find_all('a'):
                    text = a.get_text(strip=True)
                    href = a.get('href', '')
                    if text and href.startswith('/wiki/') and ':' not in href.split('/wiki/')[-1]:
                        if text not in ergebnisse:
                            ergebnisse.append(text)
                el = el.find_next_sibling()
    return ergebnisse

def extrahiere_sprachlinks(soup):
    sprachen = {}
    lang_container = soup.find(id="p-lang-btn") or soup.find(id="p-lang")
    if lang_container:
        for li in lang_container.find_all('li'):
            a = li.find('a')
            if a and a.get('href') and a.get('lang'):
                sprachen[a.get('lang')] = a.get('href')
    return sprachen

def scrape_wikipedia_advanced(url):
    headers = {"User-Agent": "MeinAdvancedStreamlitBot/1.0 (Kontakt: mein_email@domain.com)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 404:
            return None, "Link oder Begriff konnte nicht gefunden werden"
        if response.status_code != 200:
            return None, f"Fehler: Status-Code {response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        titel_element = soup.find(id="firstHeading")
        if not titel_element:
            return None, "Konnte den Titel der Seite nicht finden."
        titel = titel_element.text
        
        such_bereich = soup.find(id="mw-content-text")
        absaetze = such_bereich.find_all('p')
        gesamter_text = "\n\n".join([p.text.strip() for p in absaetze if p.text.strip()])
        
        ignorierte_sektionen = ["Einzelnachweise", "Literatur", "Weblinks", "Siehe auch", "Inhaltsverzeichnis", "Anmerkungen", "Weblinks und Quellen"]
        struktur = []
        aktuelle_h2 = None
        
        alle_headlinetags = such_bereich.find_all(['h2', 'h3'])
        
        for elem in alle_headlinetags:
            headline_span = elem.find(class_="mw-headline")
            text = headline_span.text.strip() if headline_span else elem.text.replace('[Bearbeiten]', '').strip()
            
            if text in ignorierte_sektionen or not text:
                continue
                
            if elem.name == 'h2':
                aktuelle_h2 = text
                struktur.append({"typ": "h2", "text": text, "kinder": []})
            elif elem.name == 'h3' and aktuelle_h2:
                if struktur:
                    struktur[-1]["kinder"].append(text)

        quellen_daten = {"Einzelnachweise": [], "Literatur": [], "Weblinks": []}
        
        for i, headline in enumerate(alle_headlinetags):
            headline_span = headline.find(class_="mw-headline")
            h_text = headline_span.text.strip() if headline_span else headline.text.strip()
            
            schluessel = None
            if "einzelnachweis" in h_text.lower():
                schluessel = "Einzelnachweise"
            elif "literatur" in h_text.lower():
                schluessel = "Literatur"
            elif "weblink" in h_text.lower() or "internetquelle" in h_text.lower():
                schluessel = "Weblinks"
                
            if schluessel:
                start_element = get_traversal_start(headline)
                naechste_headline_roh = alle_headlinetags[i+1] if i+1 < len(alle_headlinetags) else None
                stop_element = get_traversal_start(naechste_headline_roh) if naechste_headline_roh else None
                
                aktuelles_element = start_element.find_next_sibling()
                while aktuelles_element and aktuelles_element != stop_element:
                    listen_eintraege = aktuelles_element.find_all('li')
                    for li in listen_eintraege:
                        text = li.text.strip()
                        text = text.replace('↑ ', '').strip()
                        if text and text not in quellen_daten[schluessel]:
                            quellen_daten[schluessel].append(text)
                    aktuelles_element = aktuelles_element.find_next_sibling()

        infobox_daten = extrahiere_infobox(soup)
        infobox_roh_gefunden = soup.find('table', class_='infobox') is not None
        kategorien = extrahiere_kategorien(soup)
        siehe_auch = extrahiere_siehe_auch(alle_headlinetags)
        sprachlinks = extrahiere_sprachlinks(soup)
        
        bilder_urls = extrahiere_bilder(soup)
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
            "url": url
        }, None
        
    except Exception as e:
        return None, f"Ein Fehler ist aufgetreten: {str(e)}"

def scrape_sprachversion_kompakt(url):
    headers = {"User-Agent": "MeinAdvancedStreamlitBot/1.0 (Kontakt: mein_email@domain.com)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, f"Status-Code {response.status_code}"

        soup = BeautifulSoup(response.text, 'html.parser')
        titel_element = soup.find(id="firstHeading")
        titel = titel_element.text if titel_element else "Unbekannt"

        such_bereich = soup.find(id="mw-content-text")
        if not such_bereich:
            return None, "Kein Hauptinhalt gefunden."

        absaetze = such_bereich.find_all('p')
        gesamter_text = "\n\n".join([p.text.strip() for p in absaetze if p.text.strip()])

        alle_headlinetags = such_bereich.find_all(['h2', 'h3'])
        ueberschriften = []
        nicht_inhalt_keywords = [
            "reference", "einzelnachweis", "see also", "siehe auch", "external link",
            "weblink", "literatur", "bibliography", "further reading", "notes",
            "anmerkung", "source", "quelle", "citation", "footnote"
        ]

        def ist_inhaltsabschnitt(text):
            return not any(kw in text.lower() for kw in nicht_inhalt_keywords)

        for elem in alle_headlinetags:
            headline_span = elem.find(class_="mw-headline")
            text = headline_span.text.strip() if headline_span else elem.text.replace('[edit]', '').strip()
            if text and ist_inhaltsabschnitt(text):
                ueberschriften.append({"ebene": elem.name, "text": text})

        anzahl_quellen = 0
        ref_container = such_bereich.find_all(class_=["reflist", "references"])
        oberste_container = [
            c for c in ref_container
            if not any(c in anderer.find_all(class_=["reflist", "references"]) for anderer in ref_container if anderer is not c)
        ]
        for container in oberste_container:
            anzahl_quellen += len(container.find_all('li'))

        return {
            "titel": titel,
            "wortanzahl": len(gesamter_text.split()),
            "anzahl_abschnitte": len([h for h in ueberschriften if h["ebene"] == "h2"]),
            "ueberschriften": ueberschriften,
            "anzahl_quellen": anzahl_quellen
        }, None

    except Exception as e:
        return None, f"Fehler beim Abruf: {str(e)}"

def generiere_zitation(titel, url, zitierstil="Harvard"):
    heute = datetime.now()
    monate_de = ["Januar", "Februar", "März", "April", "Mai", "Juni",
                 "Juli", "August", "September", "Oktober", "November", "Dezember"]
    monate_en = ["January", "February", "March", "April", "May", "June",
                 "July", "August", "September", "October", "November", "December"]
    abrufdatum_de = f"{heute.day}. {monate_de[heute.month - 1]} {heute.year}"
    abrufdatum_de_kurz = heute.strftime("%d.%m.%Y")
    abrufdatum_en = f"{monate_en[heute.month - 1]} {heute.day}, {heute.year}"
    jahr = heute.year

    formate = {
        "Harvard": f"Wikipedia ({jahr}) '{titel}', *Wikipedia, Die freie Enzyklopädie*. Verfügbar unter: {url} (Abgerufen: {abrufdatum_de_kurz}).",
        "APA 7": f"Wikipedia-Autoren. ({jahr}, {monate_de[heute.month - 1]} {heute.day}). *{titel}*. In Wikipedia. Abgerufen am {abrufdatum_de}, von {url}",
        "MLA 9": f'"{titel}." *Wikipedia*, Wikimedia Foundation, {abrufdatum_en}, {url}.',
        "Chicago (Autor-Datum)": f"Wikipedia-Autoren. {jahr}. \"{titel}.\" Wikipedia, Die freie Enzyklopädie. Zuletzt geändert {jahr}. Zugriff am {abrufdatum_de}. {url}.",
        "Chicago (Notes-Bibliography)": f"Wikipedia-Autoren, \"{titel},\" Wikipedia, Die freie Enzyklopädie, zuletzt geändert {jahr}, abgerufen am {abrufdatum_de}, {url}.",
        "IEEE": f"Wikipedia-Autoren, \"{titel},\" *Wikipedia, Die freie Enzyklopädie*, {jahr}. [Online]. Verfügbar: {url}. [Zugriff: {abrufdatum_de_kurz}].",
        "Vancouver": f"Wikipedia-Autoren. {titel} [Internet]. Wikipedia, Die freie Enzyklopädie; {jahr} [zitiert {abrufdatum_de_kurz}]. Verfügbar von: {url}",
        "DIN 1505-2": f"{titel}. In: Wikipedia, Die freie Enzyklopädie. Bearbeitungsstand: {jahr}. URL: {url} (Abgerufen: {abrufdatum_de_kurz})",
        "ÖNORM": f"Wikipedia ({jahr}): {titel}. Online: {url} (Zugriff am {abrufdatum_de_kurz})",
        "Kurzform (Inline)": f"({titel}, Wikipedia {jahr})",
    }
    return formate.get(zitierstil, formate["Harvard"])


# --- STREAMLIT UI ---
st.set_page_config(page_title="Wiki Analyzer", page_icon="🧠", layout="wide")

st.title("🧠 Wikipedia Seiten Analyzer")
st.markdown("Suche nach einem Thema oder gib einen Link ein, um die Architektur des Artikels zu analysieren.")

nutzer_eingabe = st.text_input(
    "Wikipedia URL oder Suchbegriff", 
    placeholder="z. B. 'Albert Einstein' oder direkten Link einfügen..."
)

if "daten" not in st.session_state:
    st.session_state["daten"] = None
if "fehler" not in st.session_state:
    st.session_state["fehler"] = None

if st.button("Artikel analysieren", type="primary"):
    if not nutzer_eingabe:
        st.warning("Bitte gib eine URL oder einen Suchbegriff ein!")
    else:
        keys_to_clear = ["sprachvergleich_ergebnis", "sprachvergleich_fehler", "sprachvergleich_sprache", "chat_history"]
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]

        with st.spinner("Suche und analysiere Artikel..."):
            aufgeloeste_url, such_fehler = resolve_wikipedia_input(nutzer_eingabe)
            if such_fehler:
                daten, fehler = None, such_fehler
            else:
                daten, fehler = scrape_wikipedia_advanced(aufgeloeste_url)
                
            st.session_state["daten"] = daten
            st.session_state["fehler"] = fehler

daten = st.session_state["daten"]
fehler = st.session_state["fehler"]

if daten is not None or fehler is not None:
    if fehler:
        st.error(fehler)
    else:
        st.success(f"Analyse abgeschlossen für: **{daten['titel']}**")
            
        tab_mindmap, tab_zeitleiste, tab_galerie, tab_chat, tab_quellen, tab_info, tab_sprachen, tab_zitat, tab_text = st.tabs([
            "🗺️ Mindmap", 
            "⏱️ Zeitleiste",
            "🖼️ Galerie",
            "🤖 KI-Chat",
            "📚 Quellen", 
            "ℹ️ Übersicht",
            "🌍 Sprachen",
            "🖊️ Zitieren",
            "📄 Text"
        ])
            
        with tab_mindmap:
            st.subheader("Automatische Inhalts-Struktur")
            if not daten['struktur']:
                st.info("Keine tieferen Überschriften zur Strukturierung gefunden.")
            else:
                dot = Digraph(comment=daten['titel'])
                dot.attr(rankdir='LR') 
                dot.node('Haupt', wrap_fuer_mindmap(daten['titel'], breite=25), 
                         style='filled', color='lightblue', shape='ellipse')
                    
                for i, h2 in enumerate(daten['struktur']):
                    h2_id = f"h2_{i}"
                    dot.node(h2_id, wrap_fuer_mindmap(h2['text'], breite=20), 
                             style='filled', color='lightgray', shape='box')
                    dot.edge('Haupt', h2_id)
                    for j, h3 in enumerate(h2['kinder']):
                        h3_id = f"h3_{i}_{j}"
                        dot.node(h3_id, wrap_fuer_mindmap(h3, breite=18), shape='plaintext')
                        dot.edge(h2_id, h3_id)
                st.graphviz_chart(dot)

        with tab_zeitleiste:
            st.subheader("Chronologische Zeitleiste")
            st.markdown("Automatisch extrahierte historische Eckdaten aus dem Text.")
            if not daten['zeitleiste']:
                st.info("Es konnten keine eindeutigen Jahreszahlen im Text gefunden werden.")
            else:
                for eintrag in daten['zeitleiste']:
                    st.markdown(f"**{eintrag['jahr']}** — {eintrag['text']}")

        with tab_galerie:
            st.subheader("Artikel-Galerie")
            if not daten['bilder']:
                st.info("Keine Bilder in diesem Artikel gefunden.")
            else:
                cols = st.columns(3)
                for i, img_url in enumerate(daten['bilder']):
                    with cols[i % 3]:
                        st.image(img_url, use_container_width=True)

        with tab_chat:
            st.subheader("🤖 Frag den Artikel (KI-Assistent)")
            st.markdown("Nutze KI, um konkrete Fragen an diesen Wikipedia-Artikel zu stellen. *(Benötigt einen kostenlosen Google Gemini API-Key)*")
            
            api_key = st.text_input("Gemini API Key eingeben (wird nach Neuladen der Seite gelöscht):", type="password")
            st.markdown("[Hier kostenlosen API Key erstellen](https://aistudio.google.com/app/apikey)")
            st.divider()

            if "chat_history" not in st.session_state:
                st.session_state.chat_history = []
                
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    
            user_q = st.chat_input("Deine Frage zum Artikel...")
            if user_q:
                if not api_key:
                    st.error("Bitte gib oben erst deinen API Key ein.")
                else:
                    st.session_state.chat_history.append({"role": "user", "content": user_q})
                    with st.chat_message("user"):
                        st.markdown(user_q)
                    
                    with st.chat_message("assistant"):
                        with st.spinner("Analysiere Text..."):
                            try:
                                genai.configure(api_key=api_key)
                                model = genai.GenerativeModel('gemini-1.5-flash')
                                context_text = daten['text'][:60000]
                                prompt = (
                                    f"Du bist ein hilfreicher Forschungs-Assistent. Beantworte die folgende Frage des Nutzers "
                                    f"AUSSCHLIESSLICH basierend auf dem untenstehenden Wikipedia-Text. Erfinde nichts dazu. "
                                    f"Wenn die Antwort nicht im Text steht, sage das deutlich.\n\n"
                                    f"WIKIPEDIA TEXT:\n{context_text}\n\n"
                                    f"FRAGE DES NUTZERS:\n{user_q}"
                                )
                                response = model.generate_content(prompt)
                                st.markdown(response.text)
                                st.session_state.chat_history.append({"role": "assistant", "content": response.text})
                            except Exception as e:
                                st.error(f"Fehler bei der KI-Anfrage. Bitte prüfe deinen API-Key. Details: {e}")
            
        with tab_quellen:
            st.subheader("Literaturnachweise, Quellen und Weblinks")
            suchbegriff = st.text_input("🔍 Quellen durchsuchen", key="quellen_suche")

            def filtere(liste, begriff):
                if not begriff: return liste
                return [eintrag for eintrag in liste if begriff.lower() in eintrag.lower()]

            einzelnachweise_gefiltert = filtere(daten['quellen']['Einzelnachweise'], suchbegriff)
            literatur_gefiltert = filtere(daten['quellen']['Literatur'], suchbegriff)
            weblinks_gefiltert = filtere(daten['quellen']['Weblinks'], suchbegriff)

            if suchbegriff:
                st.caption(f"{len(einzelnachweise_gefiltert) + len(literatur_gefiltert) + len(weblinks_gefiltert)} Treffer")
                
            spalte1, spalte2, spalte3 = st.columns(3)
                
            with spalte1:
                st.markdown("### 📝 Einzelnachweise")
                if einzelnachweise_gefiltert:
                    for ref in einzelnachweise_gefiltert[:40]: st.caption(ref)
                    if len(einzelnachweise_gefiltert) > 40: st.text(f"... und {len(einzelnachweise_gefiltert)-40} weitere.")
                elif suchbegriff: st.info("Keine Treffer.")
                else: st.info("Keine direkten Einzelnachweise gefunden.")
                        
            with spalte2:
                st.markdown("### 📖 Literatur")
                if literatur_gefiltert:
                    for lit in literatur_gefiltert: st.markdown(f"- {lit}")
                elif suchbegriff: st.info("Keine Treffer.")
                else: st.info("Keine Literatureinträge gefunden.")
                        
            with spalte3:
                st.markdown("### 🔗 Weblinks")
                if weblinks_gefiltert:
                    for link in weblinks_gefiltert: st.markdown(f"- {link}")
                elif suchbegriff: st.info("Keine Treffer.")
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
                    for eintrag in daten['siehe_auch']: st.markdown(f"- {eintrag}")
                else:
                    st.info("Kein 'Siehe auch'-Abschnitt gefunden.")

            with ueb_spalte2:
                st.markdown("### 🏷️ Kategorien")
                if daten['kategorien']:
                    for kat in daten['kategorien']: st.markdown(f"`{kat}`")
                else:
                    st.info("Keine Kategorien gefunden.")

        with tab_sprachen:
            st.subheader("Artikel in anderen Sprachen vergleichen")
            if not daten['sprachlinks']:
                st.info("Keine anderssprachigen Versionen gefunden.")
            else:
                sprach_namen = {
                    "en": "Englisch", "fr": "Französisch", "es": "Spanisch", 
                    "it": "Italienisch", "nl": "Niederländisch", "pl": "Polnisch",
                    "ru": "Russisch", "ja": "Japanisch", "zh": "Chinesisch"
                }
                optionen = {f"{sprach_namen.get(code, code.upper())} ({code})": code for code in daten['sprachlinks'].keys()}
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
            zitierstile = ["Harvard", "APA 7", "MLA 9", "Chicago (Autor-Datum)", "DIN 1505-2", "Kurzform (Inline)"]
            gewaehlter_stil = st.selectbox("Zitierformat", options=zitierstile, key="zitierstil_auswahl")
            zitat_text = generiere_zitation(daten['titel'], daten['url'], zitierstil=gewaehlter_stil)
            st.text_area("Generierte Zitation", value=zitat_text, height=100)

        with tab_text:
            st.subheader("Extrahierter Fließtext")
            st.download_button("💾 Textdatei (.txt) herunterladen", data=daten['text'], file_name=f"{daten['titel']}.txt")
            st.text_area("Textvorschau", value=daten['text'], height=400, disabled=True)