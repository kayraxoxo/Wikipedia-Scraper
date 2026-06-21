import streamlit as st
import requests
from bs4 import BeautifulSoup
from graphviz import Digraph
from datetime import datetime
import urllib.parse
import textwrap

# --- ERWEITERTE & ULTRAROBUSTE SCRAPER LOGIK ---

def get_traversal_start(headline_elem):
    """Liefert das Element, ab dem wir mit find_next_sibling() weitersuchen müssen.
    Berücksichtigt sowohl altes als auch neues (mw-heading Wrapper) HTML.
    Siehe Erklärung weiter unten in scrape_wikipedia_advanced()."""
    parent = headline_elem.parent
    if parent and parent.name == "div" and parent.get("class") and \
       any("mw-heading" in c for c in parent.get("class")):
        return parent
    return headline_elem


def wrap_fuer_mindmap(text, breite=20, max_zeilen=4):
    """Bricht lange Überschriften für die Mindmap in mehrere Zeilen um, statt sie
    mit '...' abzuschneiden. So bleibt der volle Titel lesbar. Nur bei wirklich
    extrem langen Texten (mehr als max_zeilen Zeilen) wird am Ende gekürzt,
    damit einzelne Knoten nicht die ganze Grafik sprengen."""
    zeilen = textwrap.wrap(text, width=breite) or [text]
    if len(zeilen) > max_zeilen:
        zeilen = zeilen[:max_zeilen]
        zeilen[-1] = zeilen[-1].rstrip() + " …"
    return '\n'.join(zeilen)


def extrahiere_infobox(soup):
    """Extrahiert die Infobox (rechte Faktenbox) als Key-Value-Dict.
    Unterstützt zwei verschiedene MediaWiki-Infobox-Strukturen:
    1. Spezialisierte Vorlagen (z. B. Infobox Sprache, Infobox Militärischer Konflikt):
       <th>Label</th><td>Wert</td>
    2. Generische Vorlage:Infobox (z. B. bei "Deutsche Sprache" verwendet):
       <td class="infobox-label">Label</td><td class="infobox-data">Wert</td>
       (kein <th>, sondern zwei <td>!)
    Ohne Unterstützung für Muster 2 bleibt die Infobox bei Artikeln, die die generische
    Vorlage nutzen, komplett leer, obwohl eine Infobox-Tabelle gefunden wird.
    Zusätzlich robust gegen: verschachtelte Subbox-Tabellen (z. B. Bild+Bildunterschrift),
    reine Bild-Zeilen, und rowspan-Fortsetzungszeilen (Zelle ohne eigenes Label, die
    an den vorherigen Key angehängt wird)."""
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
            # Muster 1: klassisches <th>Label</th><td>Wert</td>
            key = th.get_text(separator=' ', strip=True)
            val = alle_tds[0].get_text(separator=' ', strip=True)
            if key and val:
                daten[key] = val
                letzter_key = key

        elif not th and len(alle_tds) >= 2:
            # Muster 2: generisches Vorlage:Infobox mit zwei <td> (Label + Daten)
            label_td = row.find('td', class_='infobox-label', recursive=False) or alle_tds[0]
            daten_td = row.find('td', class_='infobox-data', recursive=False) or alle_tds[1]
            key = label_td.get_text(separator=' ', strip=True)
            val = daten_td.get_text(separator=' ', strip=True)
            if key and val:
                daten[key] = val
                letzter_key = key

        elif not th and len(alle_tds) == 1 and letzter_key:
            # rowspan-Fortsetzungszeile: nur eine Zelle ohne eigenes Label,
            # gehört zum letzten Key (gilt für beide Strukturmuster oben)
            zusatz = alle_tds[0].get_text(separator=' ', strip=True)
            if zusatz:
                daten[letzter_key] = f"{daten[letzter_key]} {zusatz}".strip()
        # Zeilen mit nur th (z. B. reine Sektionsüberschriften/Bild-Zeilen ohne Kontext)
        # werden bewusst übersprungen.

    return daten


def extrahiere_kategorien(soup):
    """Extrahiert die Wikipedia-Kategorien am Seitenende."""
    catlinks = soup.find(id="catlinks")
    if not catlinks:
        return []
    return [a.get_text(strip=True) for a in catlinks.find_all('a') if a.get_text(strip=True)]


def extrahiere_siehe_auch(alle_headlinetags):
    """Extrahiert die Links aus dem Abschnitt 'Siehe auch'."""
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
                    # Nur echte Artikel-Links (kein Kategorie:/Datei:/etc. Namespace)
                    if text and href.startswith('/wiki/') and ':' not in href.split('/wiki/')[-1]:
                        if text not in ergebnisse:
                            ergebnisse.append(text)
                el = el.find_next_sibling()
    return ergebnisse


def extrahiere_sprachlinks(soup):
    """Extrahiert verfügbare Sprachversionen des Artikels: {sprachcode: url}."""
    sprachen = {}
    # Modernes Vector-2022-Skin nutzt p-lang-btn, älteres Skin p-lang
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
        if response.status_code != 200:
            return None, f"Fehler: Status-Code {response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        titel_element = soup.find(id="firstHeading")
        if not titel_element:
            return None, "Konnte den Titel der Seite nicht finden."
        titel = titel_element.text
        
        # 1. Haupttext extrahieren
        such_bereich = soup.find(id="mw-content-text")
        absaetze = such_bereich.find_all('p')
        gesamter_text = "\n\n".join([p.text.strip() for p in absaetze if p.text.strip()])
        
        # 2. Struktur für Mindmap extrahieren (Überschriften h2 und h3)
        ignorierte_sektionen = ["Einzelnachweise", "Literatur", "Weblinks", "Siehe auch", "Inhaltsverzeichnis", "Anmerkungen", "Weblinks und Quellen"]
        struktur = []
        aktuelle_h2 = None
        
        # Wir holen uns alle Überschriften im gesamten Hauptbereich für Struktur und Quellen
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

        # 3. Quellen, Einzelnachweise, Literatur und Weblinks extrahieren
        #
        # WICHTIGER FIX: Seit dem MediaWiki Vector-2022-Skin werden Überschriften
        # in einen Wrapper gepackt:
        #   <div class="mw-heading mw-heading2"><h2 id="...">Text</h2></div>
        # Das <h2>/<h3> selbst hat dadurch KEINE Geschwister mehr - der eigentliche
        # Inhalt (z.B. <div class="reflist">) folgt erst als Sibling des Wrapper-Divs.
        # Daher: zuerst prüfen, ob ein passendes Eltern-Div existiert, und falls ja,
        # von DORT aus die Geschwister durchsuchen statt vom <h2>/<h3> selbst.
        quellen_daten = {"Einzelnachweise": [], "Literatur": [], "Weblinks": []}
        
        for i, headline in enumerate(alle_headlinetags):
            headline_span = headline.find(class_="mw-headline")
            h_text = headline_span.text.strip() if headline_span else headline.text.strip()
            
            schluessel = None
            # Flexiblere Textprüfung mittels ".lower()", um Namensabweichungen abzufangen
            if "einzelnachweis" in h_text.lower():
                schluessel = "Einzelnachweise"
            elif "literatur" in h_text.lower():
                schluessel = "Literatur"
            elif "weblink" in h_text.lower() or "internetquelle" in h_text.lower():
                schluessel = "Weblinks"
                
            if schluessel:
                # Start- und Stopp-Element jeweils über get_traversal_start() ermitteln,
                # damit beide Seiten konsistent auf derselben "Ebene" (Wrapper-Div oder
                # direktes Element) verglichen werden.
                start_element = get_traversal_start(headline)
                
                naechste_headline_roh = alle_headlinetags[i+1] if i+1 < len(alle_headlinetags) else None
                stop_element = get_traversal_start(naechste_headline_roh) if naechste_headline_roh else None
                
                aktuelles_element = start_element.find_next_sibling()
                # Scanne den kompletten Raum bis zur nächsten Überschrift (bzw. deren Wrapper)
                while aktuelles_element and aktuelles_element != stop_element:
                    # Sucht radikal nach jedem Listeneintrag (li), egal wie tief er verschachtelt ist
                    listen_eintraege = aktuelles_element.find_all('li')
                    for li in listen_eintraege:
                        text = li.text.strip()
                        # Bereinigt typische Wikipedia-Editier-Reste
                        text = text.replace('↑ ', '').strip()
                        if text and text not in quellen_daten[schluessel]:
                            quellen_daten[schluessel].append(text)
                            
                    aktuelles_element = aktuelles_element.find_next_sibling()

        # 4. Infobox extrahieren (strukturierte Fakten, z.B. bei Personen/Ländern/Software)
        infobox_daten = extrahiere_infobox(soup)
        # Diagnose-Flag: wurde überhaupt eine Tabelle mit Klasse "infobox" gefunden?
        # Hilft zu unterscheiden zwischen "Artikel hat keine Infobox" und
        # "Infobox gefunden, aber Extraktion lieferte aus Strukturgründen nichts".
        infobox_roh_gefunden = soup.find('table', class_='infobox') is not None

        # 5. Kategorien extrahieren (thematische Einordnung am Seitenende)
        kategorien = extrahiere_kategorien(soup)

        # 6. "Siehe auch"-Links extrahieren (verwandte Artikel)
        siehe_auch = extrahiere_siehe_auch(alle_headlinetags)

        # 7. Verfügbare Sprachversionen extrahieren (für Sprachvergleich)
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
    """Leichtgewichtiger Scrape einer fremdsprachigen Artikelversion für den Vergleich.
    Holt nur Titel, Anzahl Abschnitte, Abschnitts-Überschriften, Textlänge und Quellenzahl -
    spart Zeit gegenüber dem vollen scrape_wikipedia_advanced()."""
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
        # Mehrsprachige Liste von Nicht-Inhalts-Abschnitten, damit der Abschnittsvergleich
        # nur inhaltliche Kapitel zählt (analog zur deutschen ignorierte_sektionen-Liste
        # in scrape_wikipedia_advanced) - deckt die gängigsten Wikipedia-Sprachen ab.
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

        # Grobe Quellenzählung: <li>-Elemente innerhalb von reflist/references-Containern,
        # aber NUR vom jeweils äußersten/obersten passenden Container aus zählen, damit
        # verschachtelte Treffer (z.B. <div class="reflist"><ol class="references">) nicht
        # doppelt gezählt werden.
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
    """Erzeugt eine zitierfähige Quellenangabe im gewünschten Stil.
    Unterstützt die 10 in Studium/Recherche gängigsten Zitierformate.
    HINWEIS: Der Parameter heißt bewusst 'zitierstil' und nicht 'format',
    um keine Verwechslung mit Pythons eingebauter format()-Funktion zu riskieren."""
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
        "Harvard": (
            f"Wikipedia ({jahr}) '{titel}', *Wikipedia, Die freie Enzyklopädie*. "
            f"Verfügbar unter: {url} (Abgerufen: {abrufdatum_de_kurz})."
        ),
        "APA 7": (
            f"Wikipedia-Autoren. ({jahr}, {monate_de[heute.month - 1]} {heute.day}). "
            f"*{titel}*. In Wikipedia. Abgerufen am {abrufdatum_de}, von {url}"
        ),
        "MLA 9": (
            f'"{titel}." *Wikipedia*, Wikimedia Foundation, {abrufdatum_en}, {url}.'
        ),
        "Chicago (Autor-Datum)": (
            f"Wikipedia-Autoren. {jahr}. \"{titel}.\" Wikipedia, Die freie Enzyklopädie. "
            f"Zuletzt geändert {jahr}. Zugriff am {abrufdatum_de}. {url}."
        ),
        "Chicago (Notes-Bibliography)": (
            f"Wikipedia-Autoren, \"{titel},\" Wikipedia, Die freie Enzyklopädie, "
            f"zuletzt geändert {jahr}, abgerufen am {abrufdatum_de}, {url}."
        ),
        "IEEE": (
            f"Wikipedia-Autoren, \"{titel},\" *Wikipedia, Die freie Enzyklopädie*, {jahr}. "
            f"[Online]. Verfügbar: {url}. [Zugriff: {abrufdatum_de_kurz}]."
        ),
        "Vancouver": (
            f"Wikipedia-Autoren. {titel} [Internet]. Wikipedia, Die freie Enzyklopädie; {jahr} "
            f"[zitiert {abrufdatum_de_kurz}]. Verfügbar von: {url}"
        ),
        "DIN 1505-2": (
            f"{titel}. In: Wikipedia, Die freie Enzyklopädie. Bearbeitungsstand: {jahr}. "
            f"URL: {url} (Abgerufen: {abrufdatum_de_kurz})"
        ),
        "ÖNORM": (
            f"Wikipedia ({jahr}): {titel}. Online: {url} (Zugriff am {abrufdatum_de_kurz})"
        ),
        "Kurzform (Inline)": (
            f"({titel}, Wikipedia {jahr})"
        ),
    }
    return formate.get(zitierstil, formate["Harvard"])


# --- STREAMLIT UI ---
st.set_page_config(page_title="Wiki Analyzer", page_icon="🧠", layout="wide")

st.title("🧠 Wikipedia Seiten Analyzer")
st.markdown("Analysiere die Architektur eines Artikels, visualisiere ihn als Mindmap und extrahiere alle Belege.")

# Eingabefeld für die URL
ziel_url = st.text_input(
    "Wikipedia URL", 
    placeholder="https://de.wikipedia.org/wiki/Python_(Programmiersprache)"
)

# WICHTIG: Streamlit führt bei JEDEM Button-Klick (auch in anderen Tabs, z.B.
# "Vergleichen" im Sprachvergleich-Tab) das komplette Skript neu aus. Ohne
# session_state würde "daten" dabei verloren gehen, weil der
# "Artikel analysieren"-Button dann nicht mehr aktiv ist und der else-Zweig
# (mit der gesamten Tab-Anzeige) übersprungen wird. Wir speichern das
# Analyseergebnis daher dauerhaft in session_state, sodass es jeden Rerun übersteht.
if "daten" not in st.session_state:
    st.session_state["daten"] = None
if "fehler" not in st.session_state:
    st.session_state["fehler"] = None

if st.button("Artikel analysieren", type="primary"):
    if not ziel_url:
        st.warning("Bitte gib eine URL ein!")
    else:
        with st.spinner("Analysiere Artikellayout und extrahiere Referenzen..."):
            daten, fehler = scrape_wikipedia_advanced(ziel_url)
        st.session_state["daten"] = daten
        st.session_state["fehler"] = fehler

# Ab hier wird IMMER aus session_state gelesen statt aus lokalen Variablen,
# damit Sprachvergleich/Zitieren (eigene Buttons) den Analyse-Stand nicht verlieren.
daten = st.session_state["daten"]
fehler = st.session_state["fehler"]

if daten is not None or fehler is not None:
            
    if fehler:
        st.error(fehler)
    else:
        st.success(f"Analyse abgeschlossen für: **{daten['titel']}**")
            
        # Layout mit Tabs für eine saubere Struktur
        tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "🗺️ Inhalts-Mindmap", 
            "📚 Quellen & Verweise", 
            "📄 Rohtext & Download",
            "ℹ️ Übersicht",
            "🌍 Sprachvergleich",
            "🖊️ Zitieren"
        ])
            
        # TAB 1: Die automatische Mindmap
        with tab1:
            st.subheader("Automatische Inhalts-Struktur (Mindmap)")
            if not daten['struktur']:
                st.info("Keine tieferen Überschriften zur Strukturierung gefunden.")
            else:
                dot = Digraph(comment=daten['titel'])
                dot.attr(rankdir='LR')  # Left-to-Right Layout
                    
                # Zentrumsknoten (auch der Haupttitel bekommt Umbruch statt Abschneiden)
                dot.node('Haupt', wrap_fuer_mindmap(daten['titel'], breite=25), 
                         style='filled', color='lightblue', shape='ellipse', 
                         tooltip=daten['titel'])
                    
                # Äste (h2) und Zweige (h3) zeichnen
                for i, h2 in enumerate(daten['struktur']):
                    h2_id = f"h2_{i}"
                    # Lange Überschriften werden umgebrochen statt abgeschnitten;
                    # der Tooltip zeigt beim Hovern zusätzlich den vollständigen Text.
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
            
        # TAB 2: Der jetzt korrigierte Quellen-Bereich
        with tab2:
            st.subheader("Literaturnachweise, Quellen und Weblinks")

            # Idee 8: Suchfeld zum Filtern aller Quellenarten (Autor, Jahr, Stichwort...)
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
                    # Auf 40 Einträge deckeln, um das UI flüssig zu halten
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

        # TAB 3: Rohtext & Download
        with tab3:
            st.subheader("Extrahierter Fließtext")
            st.download_button(
                label="💾 Textdatei (.txt) herunterladen",
                data=daten['text'],
                file_name=f"{daten['titel'].replace(' ', '_')}.txt",
                mime="text/plain"
            )
            st.text_area(label="Textvorschau", value=daten['text'], height=400, disabled=True)

        # TAB 4: Übersicht - Infobox, Kategorien, Siehe auch (Ideen 1, 2, 3)
        with tab4:
            st.subheader("Schnellübersicht")

            ueb_spalte1, ueb_spalte2 = st.columns([3, 2])

            with ueb_spalte1:
                st.markdown("### 📋 Infobox")
                if daten['infobox']:
                    for key, value in daten['infobox'].items():
                        st.markdown(f"**{key}:** {value}")
                elif daten.get('infobox_roh_gefunden'):
                    st.warning(
                        "Eine Infobox wurde gefunden, aber ihre Struktur konnte nicht "
                        "ausgelesen werden. Das kann bei ungewöhnlich aufgebauten Infoboxen "
                        "(z. B. komplexe Sprach- oder Länder-Infoboxen) vorkommen."
                    )
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

        # TAB 5: Sprachvergleich (Idee 5)
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
                    
                gewaehlte_anzeige = st.selectbox(
                    "Mit welcher Sprachversion vergleichen?", 
                    options=list(optionen.keys())
                )
                    
                if st.button("Vergleichen", key="sprachvergleich_btn"):
                    gewaehlter_code = optionen[gewaehlte_anzeige]
                    vergleichs_url = daten['sprachlinks'][gewaehlter_code]
                        
                    with st.spinner(f"Lade {gewaehlte_anzeige} Version..."):
                        vergleich, vfehler = scrape_sprachversion_kompakt(vergleichs_url)
                    
                    # In session_state ablegen, damit das Ergebnis auch Reruns durch
                    # andere Widgets (z.B. die Quellen-Suche in Tab 2) übersteht.
                    st.session_state["sprachvergleich_ergebnis"] = vergleich
                    st.session_state["sprachvergleich_fehler"] = vfehler
                    st.session_state["sprachvergleich_sprache"] = gewaehlte_anzeige

                # Immer aus session_state lesen statt nur direkt nach dem Klick anzuzeigen
                vergleich = st.session_state.get("sprachvergleich_ergebnis")
                vfehler = st.session_state.get("sprachvergleich_fehler")
                angezeigte_sprache = st.session_state.get("sprachvergleich_sprache")

                if vfehler:
                    st.error(f"Konnte Vergleichsversion nicht laden: {vfehler}")
                elif vergleich:
                    # Eigene Metriken für den Originalartikel berechnen
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
                        st.metric("Wortanzahl", f"{eigene_wortanzahl:,}", 
                                  delta=f"{eigene_wortanzahl - vergleich['wortanzahl']:,} vs. {angezeigte_sprache}")
                    with m2:
                        st.metric("Anzahl Abschnitte", eigene_h2_anzahl, 
                                  delta=eigene_h2_anzahl - vergleich['anzahl_abschnitte'])
                    with m3:
                        st.metric("Anzahl Quellen (ca.)", eigene_quellenzahl, 
                                  delta=eigene_quellenzahl - vergleich['anzahl_quellen'])
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

        # TAB 6: Zitierfähige Quellenangabe (Idee 6)
        with tab6:
            st.subheader("Artikel zitieren")
            st.markdown("Erzeugt eine zitierfähige Angabe für diesen Wikipedia-Artikel mit aktuellem Abrufdatum.")

            zitierstile = [
                "Harvard", "APA 7", "MLA 9", "Chicago (Autor-Datum)", 
                "Chicago (Notes-Bibliography)", "IEEE", "Vancouver", 
                "DIN 1505-2", "ÖNORM", "Kurzform (Inline)"
            ]
            # WICHTIG: Wir übergeben hier bewusst KEIN explizites index zusammen mit key.
            # Bei Streamlit führt die Kombination aus festem index UND key dazu, dass das
            # Widget bei jedem Rerun (ausgelöst durch IRGENDEIN anderes Widget im Skript,
            # z.B. die Quellen-Suche oder den Sprachvergleich-Button) erneut auf index
            # zurückgesetzt wird - die Auswahl des Nutzers geht dadurch faktisch verloren
            # bzw. "klemmt" auf dem ersten Wert. Ohne explizites index verwaltet Streamlit
            # den gewählten Wert zuverlässig selbst über session_state[key].
            # Harvard bleibt trotzdem das Standardformat, weil es als erstes Element der
            # Liste beim allerersten Rendern (wenn session_state[key] noch nicht existiert)
            # automatisch ausgewählt ist.
            gewaehlter_stil = st.selectbox(
                "Zitierformat", 
                options=zitierstile,
                key="zitierstil_auswahl"
            )

            zitat_text = generiere_zitation(daten['titel'], daten['url'], zitierstil=gewaehlter_stil)

            # WICHTIG: Hier bewusst KEIN key übergeben. Genau wie bei der Selectbox oben
            # führt die Kombination aus "key" UND "value" bei Streamlit dazu, dass ab dem
            # zweiten Rendern der ALTE, in session_state gespeicherte Wert gewinnt und der
            # neu berechnete "value"-Parameter ignoriert wird. Das Zitat blieb dadurch beim
            # zuerst angezeigten Text stehen, obwohl "gewaehlter_stil" sich korrekt änderte.
            # Da dieses Feld nur zur Anzeige/zum Kopieren dient (nicht zur Texteingabe durch
            # den Nutzer), ist ein eigener key ohnehin nicht nötig - ohne key wird value bei
            # jedem Rerun zuverlässig neu gerendert.
            st.text_area("Generierte Zitation (zum Kopieren markieren)", value=zitat_text, height=100)

            st.caption(
                "⚠️ Hinweis: Wikipedia gilt in vielen akademischen Kontexten nicht als "
                "zitierfähige Primärquelle. Prüfe die Anforderungen deiner Institution und "
                "ziehe nach Möglichkeit die in 'Einzelnachweise' gelisteten Originalquellen heran."
            )