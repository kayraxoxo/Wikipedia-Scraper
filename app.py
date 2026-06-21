import streamlit as st
import requests
from bs4 import BeautifulSoup
from graphviz import Digraph
from datetime import datetime
import urllib.parse
import textwrap

# --- NEU: INTELLIGENTE SUCHE & URL-AUFLÖSUNG ---
def resolve_wikipedia_input(user_input):
    """
    Prüft, ob die Eingabe eine URL ist. Wenn nicht, nutzt es die Wikipedia OpenSearch API,
    um den Begriff zum passenden Artikel-Link aufzulösen.
    """
    user_input = user_input.strip()
    
    # Fall 1: Nutzer hat direkt eine URL eingegeben
    if user_input.startswith("http://") or user_input.startswith("https://"):
        return user_input, None
    
    # Fall 2: Nutzer hat einen Suchbegriff eingegeben
    suche_url = f"https://de.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(user_input)}&limit=1&namespace=0&format=json"
    headers = {"User-Agent": "MeinAdvancedStreamlitBot/1.0 (Kontakt: mein_email@domain.com)"}
    
    try:
        response = requests.get(suche_url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            # Das Datenformat von OpenSearch ist: [Suchbegriff, [Titel], [Zusammenfassung], [URLs]]
            # data[3] enthält die verknüpften URLs. Wenn sie nicht leer ist, haben wir einen Treffer!
            if len(data) > 3 and data[3]:
                gefundene_url = data[3][0]
                return gefundene_url, None
    except Exception:
        pass
        
    # Wenn die API fehlschlägt oder die Suchliste leer ist
    return None, "Link oder Begriff konnte nicht gefunden werden"


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
        response = requests.get(url, headers=headers)
        
        # NEU: Direkte Erkennung eines 404 Fehlers für saubere Ausgabe
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
            text_lower = text.lower()
            return not any(kw in text_lower for kw in nicht_inhalt_keywords)

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
            "zeichenanzahl": len(gesamter_text),
            "anzahl_abschnitte": len([h for h in ueberschriften if h["ebene"] == "h2"]),
            "ueberschriften": ueberschriften,
            "anzahl_quellen": anzahl_quellen,
            "url": url
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

# NEU: Das smarte Suchfeld
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
        with st.spinner("Suche und analysiere Artikel..."):
            
            # 1. Eingabe auflösen (Suche vs. direkter Link)
            aufgeloeste_url, such_fehler = resolve_wikipedia_input(nutzer_eingabe)
            
            if such_fehler:
                daten, fehler = None, such_fehler
            else:
                # 2. Den Artikel scrapen, falls die URL gefunden wurde
                daten, fehler = scrape_wikipedia_advanced(aufgeloeste_url)
                
            st.session_state["daten"] = daten
            st.session_state["fehler"] = fehler

daten = st.session_state["daten"]
fehler = st.session_state["fehler"]

if daten is not None or fehler is not None:
            
    if fehler:
        # Hier greift nun unsere exakte Meldung: "Link oder Begriff konnte nicht gefunden werden"
        st.error(fehler)
    else:
        st.success(f"Analyse abgeschlossen für: **{daten['titel']}**")
            
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "🗺️ Inhalts-Mindmap", 
            "📚 Quellen & Verweise", 
            "📄 Rohtext & Download",
            "ℹ️ Übersicht",
            "🌍 Sprachvergleich",
            "🖊️ Zitieren"
        ])
            
        with tab1:
            st.subheader("Automatische Inhalts-Struktur (Mindmap)")
            if not daten['struktur']:
                st.info("Keine tieferen Überschriften zur Strukturierung gefunden.")
            else:
                dot = Digraph(comment=daten['titel'])
                dot.attr(rankdir='LR') 
                    
                dot.node('Haupt', wrap_fuer_mindmap(daten['titel'], breite=25), 
                         style='filled', color='lightblue', shape='ellipse', 
                         tooltip=daten['titel'])
                    
                for i, h2 in enumerate(daten['struktur']):
                    h2_id = f"h2_{i}"
                    anzeige_text = wrap_fuer_mindmap(h2['text'], breite=20)
                    dot.node(h2_id, anzeige_text, style='filled', color='lightgray', 
                              shape='box', tooltip=h2['text'])
                    dot.edge('Haupt', h2_id)
                        
                    for j, h3 in enumerate(h2['kinder']):
                        h3_id = f"h3_{i}_{j}"
                        h3_anzeige = wrap_fuer_mindmap(h3, breite=18)
                        dot.node(h3_id, h3_anzeige, shape='plaintext', tooltip=h3)
                        dot.edge(h2_id, h3_id)
                    
                st.graphviz_chart(dot)
                st.caption("💡 Tipp: Lange Titel werden umgebrochen statt abgeschnitten. Fahre mit der Maus über einen Knoten, um den vollständigen Originaltext als Tooltip zu sehen.")
            
        with tab2:
            st.subheader("Literaturnachweise, Quellen und Weblinks")

            suchbegriff = st.text_input(
                "🔍 Quellen durchsuchen", 
                placeholder="z.B. Autorenname, Jahr oder Stichwort...",
                key="quellen_suche"
            )

            def filtere(liste, begriff):
                if not begriff:
                    return liste
                return [eintrag for eintrag in liste if begriff.lower() in eintrag.lower()]

            einzelnachweise_gefiltert = filtere(daten['quellen']['Einzelnachweise'], suchbegriff)
            literatur_gefiltert = filtere(daten['quellen']['Literatur'], suchbegriff)
            weblinks_gefiltert = filtere(daten['quellen']['Weblinks'], suchbegriff)

            if suchbegriff:
                treffer_gesamt = len(einzelnachweise_gefiltert) + len(literatur_gefiltert) + len(weblinks_gefiltert)
                st.caption(f"{treffer_gesamt} Treffer für \"{suchbegriff}\"")
                
            spalte1, spalte2, spalte3 = st.columns(3)
                
            with spalte1:
                st.markdown("### 📝 Einzelnachweise")
                if einzelnachweise_gefiltert:
                    for ref in einzelnachweise_gefiltert[:40]: 
                        st.caption(ref)
                    if len(einzelnachweise_gefiltert) > 40:
                        st.text(f"... und {len(einzelnachweise_gefiltert)-40} weitere Nachweise.")
                elif suchbegriff:
                    st.info("Keine Treffer.")
                else:
                    st.info("Keine direkten Einzelnachweise gefunden.")
                        
            with spalte2:
                st.markdown("### 📖 Literatur")
                if literatur_gefiltert:
                    for lit in literatur_gefiltert:
                        st.markdown(f"- {lit}")
                elif suchbegriff:
                    st.info("Keine Treffer.")
                else:
                    st.info("Keine Literatureinträge gefunden.")
                        
            with spalte3:
                st.markdown("### 🔗 Weblinks")
                if weblinks_gefiltert:
                    for link in weblinks_gefiltert:
                        st.markdown(f"- {link}")
                elif suchbegriff:
                    st.info("Keine Treffer.")
                else:
                    st.info("Keine externen Weblinks gefunden.")

        with tab3:
            st.subheader("Extrahierter Fließtext")
            st.download_button(
                label="💾 Textdatei (.txt) herunterladen",
                data=daten['text'],
                file_name=f"{daten['titel'].replace(' ', '_')}.txt",
                mime="text/plain"
            )
            st.text_area(label="Textvorschau", value=daten['text'], height=400, disabled=True)

        with tab4:
            st.subheader("Schnellübersicht")

            ueb_spalte1, ueb_spalte2 = st.columns([3, 2])

            with ueb_spalte1:
                st.markdown("### 📋 Infobox")
                if daten['infobox']:
                    for key, value in daten['infobox'].items():
                        st.markdown(f"**{key}:** {value}")
                elif daten.get('infobox_roh_gefunden'):
                    st.warning("Eine Infobox wurde gefunden, aber ihre Struktur konnte nicht ausgelesen werden.")
                else:
                    st.info("Keine Infobox auf dieser Seite gefunden (nicht jeder Artikel hat eine).")

                st.markdown("### 🔗 Siehe auch")
                if daten['siehe_auch']:
                    for eintrag in daten['siehe_auch']:
                        st.markdown(f"- {eintrag}")
                else:
                    st.info("Kein 'Siehe auch'-Abschnitt gefunden.")

            with ueb_spalte2:
                st.markdown("### 🏷️ Kategorien")
                if daten['kategorien']:
                    for kat in daten['kategorien']:
                        st.markdown(f"`{kat}`")
                else:
                    st.info("Keine Kategorien gefunden.")

        with tab5:
            st.subheader("Artikel in anderen Sprachen vergleichen")

            if not daten['sprachlinks']:
                st.info("Für diesen Artikel wurden keine anderssprachigen Versionen gefunden.")
            else:
                sprach_namen = {
                    "en": "Englisch", "fr": "Französisch", "es": "Spanisch", 
                    "it": "Italienisch", "nl": "Niederländisch", "pl": "Polnisch",
                    "ru": "Russisch", "ja": "Japanisch", "zh": "Chinesisch",
                    "pt": "Portugiesisch", "sv": "Schwedisch", "ar": "Arabisch"
                }
                optionen = {
                    f"{sprach_namen.get(code, code.upper())} ({code})": code 
                    for code in daten['sprachlinks'].keys()
                }
                    
                gewaehlte_anzeige = st.selectbox("Mit welcher Sprachversion vergleichen?", options=list(optionen.keys()))
                    
                if st.button("Vergleichen", key="sprachvergleich_btn"):
                    gewaehlter_code = optionen[gewaehlte_anzeige]
                    vergleichs_url = daten['sprachlinks'][gewaehlter_code]
                        
                    with st.spinner(f"Lade {gewaehlte_anzeige} Version..."):
                        vergleich, vfehler = scrape_sprachversion_kompakt(vergleichs_url)
                    
                    st.session_state["sprachvergleich_ergebnis"] = vergleich
                    st.session_state["sprachvergleich_fehler"] = vfehler
                    st.session_state["sprachvergleich_sprache"] = gewaehlte_anzeige

                vergleich = st.session_state.get("sprachvergleich_ergebnis")
                vfehler = st.session_state.get("sprachvergleich_fehler")
                angezeigte_sprache = st.session_state.get("sprachvergleich_sprache")

                if vfehler:
                    st.error(f"Konnte Vergleichsversion nicht laden: {vfehler}")
                elif vergleich:
                    eigene_wortanzahl = len(daten['text'].split())
                    eigene_h2_anzahl = len(daten['struktur'])
                    eigene_quellenzahl = (
                        len(daten['quellen']['Einzelnachweise']) +
                        len(daten['quellen']['Literatur']) +
                        len(daten['quellen']['Weblinks'])
                    )

                    st.markdown("### 📊 Metriken im Vergleich")
                    m1, m2, m3 = st.columns(3)
                    with m1:
                        st.metric("Wortanzahl", f"{eigene_wortanzahl:,}", delta=f"{eigene_wortanzahl - vergleich['wortanzahl']:,} vs. {angezeigte_sprache}")
                    with m2:
                        st.metric("Anzahl Abschnitte", eigene_h2_anzahl, delta=eigene_h2_anzahl - vergleich['anzahl_abschnitte'])
                    with m3:
                        st.metric("Anzahl Quellen (ca.)", eigene_quellenzahl, delta=eigene_quellenzahl - vergleich['anzahl_quellen'])
                    st.caption("Delta-Werte zeigen die Differenz: Original-Artikel minus Vergleichsversion.")

                    st.markdown("### 🗂️ Abschnitts-Überschriften im Vergleich")
                    vgl_spalte1, vgl_spalte2 = st.columns(2)
                        
                    with vgl_spalte1:
                        st.markdown(f"**🇩🇪 {daten['titel']}**")
                        for h2 in daten['struktur']:
                            st.markdown(f"- {h2['text']}")
                        
                    with vgl_spalte2:
                        st.markdown(f"**{angezeigte_sprache}: {vergleich['titel']}**")
                        for h in vergleich['ueberschriften']:
                            if h['ebene'] == 'h2':
                                st.markdown(f"- {h['text']}")

        with tab6:
            st.subheader("Artikel zitieren")
            st.markdown("Erzeugt eine zitierfähige Angabe für diesen Wikipedia-Artikel mit aktuellem Abrufdatum.")

            zitierstile = [
                "Harvard", "APA 7", "MLA 9", "Chicago (Autor-Datum)", 
                "Chicago (Notes-Bibliography)", "IEEE", "Vancouver", 
                "DIN 1505-2", "ÖNORM", "Kurzform (Inline)"
            ]
            
            gewaehlter_stil = st.selectbox(
                "Zitierformat", 
                options=zitierstile,
                key="zitierstil_auswahl"
            )

            zitat_text = generiere_zitation(daten['titel'], daten['url'], zitierstil=gewaehlter_stil)

            st.text_area("Generierte Zitation (zum Kopieren markieren)", value=zitat_text, height=100)
            st.caption(
                "⚠️ Hinweis: Wikipedia gilt in vielen akademischen Kontexten nicht als "
                "zitierfähige Primärquelle. Prüfe die Anforderungen deiner Institution und "
                "ziehe nach Möglichkeit die in 'Einzelnachweise' gelisteten Originalquellen heran."
            )