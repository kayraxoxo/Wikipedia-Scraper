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

# Für den PDF-Report
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

# Für die Google-ähnliche Live-Suche
from streamlit_searchbox import st_searchbox

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

# --- INTELLIGENTE SUCHE & URL-AUFLÖSUNG ---
def resolve_wikipedia_input(user_input):
    user_input = user_input.strip()
    if user_input.startswith("http://") or user_input.startswith("https://"):
        lang = get_lang_from_url(user_input)
        parsed_url = urllib.parse.urlparse(user_input)
        path_parts = parsed_url.path.split('/')
        if len(path_parts) >= 3 and path_parts[1] == 'wiki':
            titel = urllib.parse.unquote(path_parts[2]).replace('_', ' ')
            return lang, titel
        else:
            return None, "Ungültiges Wikipedia-URL-Format."
    else:
        lang = st.session_state.get("gewaehlte_sprache", "de")
        return lang, user_input

def lade_kategorie_mitglieder(kategorie_name, fortsetzung=None, anzahl=50):
    headers = {"User-Agent": USER_AGENT}
    lang = st.session_state.get("gewaehlte_sprache", "de")
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Kategorie:{kategorie_name}",
        "cmtype": "page",
        "cmlimit": str(anzahl),
        "format": "json",
    }
    if fortsetzung:
        params["cmcontinue"] = fortsetzung
    try:
        res = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params=params, headers=headers)
        if res.status_code == 200:
            data = res.json()
            pages = [p['title'] for p in data.get('query', {}).get('categorymembers', [])]
            next_cont = data.get('continue', {}).get('cmcontinue', None)
            return pages, next_cont
    except Exception:
        pass
    return [], None

# --- FEATURE-EXTRAKTOREN (Bilder & Zeitleiste) ---
def extrahiere_bilder(soup):
    such_bereich = soup.find(id="mw-content-text") or soup
    bilder = []
    gesehene_urls = set()
    if such_bereich:
        for img in such_bereich.find_all("img"):
            src = img.get("src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://wikipedia.org" + src
                
                if "static/images" in src or src.endswith(".svg"):
                    continue
                    
                width = img.get("width")
                height = img.get("height")
                try:
                    w_val = int(width) if width else 0
                    h_val = int(height) if height else 0
                    if w_val < 50 or h_val < 50:
                        continue
                except ValueError:
                    pass
                    
                alt = img.get("alt", "").strip()
                if src not in gesehene_urls:
                    gesehene_urls.add(src)
                    bilder.append({"url": src, "alt": alt})
    return bilder

def extrahiere_zeitleiste(text):
    zeitleiste = []
    text_clean = re.sub(r'\[\d+\]', '', text)
    saetze = re.split(r'(?<=[.!?]) +', text_clean)
    jahreszahl_pattern = re.compile(r'\b(1\d{3}|20\d{2})\b')
    for satz in saetze:
        match = jahreszahl_pattern.search(satz)
        if match:
            jahr = match.group(1)
            satz_clean = satz.strip()
            if len(satz_clean) > 20 and len(satz_clean) < 300:
                zeitleiste.append({"jahr": jahr, "ereignis": satz_clean})
    return sorted(zeitleiste, key=lambda x: x["jahr"])

def get_traversal_start(headline_elem):
    parent = headline_elem.parent
    if parent and parent.name == "div" and "mw-heading" in parent.get("class", []):
        return parent
    return headline_elem

def fetch_search_suggestions(query, lang="de"):
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 15,
        "namespace": 0,
        "format": "json"
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.json()[1]
    except Exception:
        return []
    return []

# --- ERWEITERTE & ULTRAROBUSTE SCRAPER LOGIK ---
def scrape_wikipedia_artikel(ziel, lang="de"):
    # Falls ziel eine URL ist, extrahieren wir zuerst Sprache und Titel
    if ziel.startswith("http://") or ziel.startswith("https://"):
        lang_detected, titel_detected = resolve_wikipedia_input(ziel)
        if lang_detected:
            lang = lang_detected
            ziel = titel_detected
        else:
            return {"fehler": titel_detected}

    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "parse",
        "page": ziel,
        "prop": "text|sections|categories|links|langlinks",
        "format": "json",
        "redirects": 1
    }
    headers = {"User-Agent": USER_AGENT}
    
    try:
        res = requests.get(api_url, params=params, headers=headers)
        if res.status_code != 200:
            return {"fehler": f"HTTP-Fehler {res.status_code} beim Abrufen der API."}
            
        data = res.json()
        if "error" in data:
            return {"fehler": f"Wikipedia-Fehler: {data['error'].get('info', 'Unbekannter Fehler')}"}
            
        parse_data = data["parse"]
        titel = parse_data["title"]
        html_content = parse_data["text"]["*"]
        
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Text extrahieren
        paragraphs = soup.find_all("p")
        volltext = "\n".join([p.get_text() for p in paragraphs])
        
        # Struktur extrahieren
        struktur = []
        for sec in parse_data.get("sections", []):
            struktur.append({
                "ebene": int(sec["toclevel"]),
                "nummer": sec["number"],
                "name": sec["line"]
            })
            
        # Kategorien extrahieren
        kategorien = [k["*"].replace("_", " ") for k in parse_data.get("categories", []) if "hidden" not in k]
        
        # Interwiki/Sprachlinks extrahieren
        sprachlinks = {}
        for l in parse_data.get("langlinks", []):
            if l["lang"] in ["de", "en", "fr", "es", "it", "nl", "pl"]:
                sprachlinks[l["lang"]] = l["*"]
                
        # Quellen extrahieren
        quellen = {"Einzelnachweise": [], "Literatur": [], "Weblinks": []}
        
        # 1. Einzelnachweise aus <ol class="references">
        ref_ol = soup.find("ol", class_="references")
        if ref_ol:
            for li in ref_ol.find_all("li"):
                text_cite = li.get_text().strip()
                text_cite = re.sub(r'↑\s*', '', text_cite)
                text_cite = re.sub(r'\s*^[a-z0-9_.-]+$', '', text_cite)
                if text_cite:
                    quellen["Einzelnachweise"].append(text_cite)
                    
        # 2. Literatur und Weblinks aus Überschriften auslesen
        for h_elem in soup.find_all(["h2", "h3", "h4", "h5", "h6"]):
            headline_span = h_elem.find("span", class_="mw-headline")
            h_text = headline_span.get_text().strip() if headline_span else h_elem.get_text().strip()
            
            kategorie_key = None
            if "literatur" in h_text.lower():
                kategorie_key = "Literatur"
            elif "weblinks" in h_text.lower() or "externer link" in h_text.lower() or "externe links" in h_text.lower():
                kategorie_key = "Weblinks"
                
            if kategorie_key:
                start_node = get_traversal_start(h_elem)
                current = start_node.next_sibling
                while current:
                    if current.name in ["h2", "h3", "h4", "h5", "h6"] or (current.name == "div" and "mw-heading" in current.get("class", [])):
                        break
                    if current.name == "ul":
                        for li in current.find_all("li"):
                            item_text = li.get_text().strip()
                            if item_text and item_text not in quellen[kategorie_key]:
                                quellen[kategorie_key].append(item_text)
                    current = current.next_sibling

        # Links für das Öffnen in neuen Tabs umschreiben
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if href.startswith("/wiki/") and not href.startswith("/wiki/Datei:") and not href.startswith("/wiki/Kategorie:"):
                a_tag["target"] = "_blank"
                a_tag["href"] = f"https://{lang}.wikipedia.org{href}"
            elif href.startswith("//"):
                a_tag["target"] = "_blank"
                a_tag["href"] = "https:" + href
            elif href.startswith("http://") or href.startswith("https://"):
                a_tag["target"] = "_blank"

        bereinigtes_html = str(soup)
        bilder = extrahiere_bilder(soup)
        zeitleiste = extrahiere_zeitleiste(volltext)
        artikel_url = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(titel.replace(' ', '_'))}"
        
        return {
            "titel": titel,
            "text": volltext,
            "html": bereinigtes_html,
            "struktur": struktur,
            "kategorien": kategorien,
            "sprachlinks": sprachlinks,
            "quellen": quellen,
            "bilder": bilder,
            "zeitleiste": zeitleiste,
            "url": artikel_url,
            "sprache": lang
        }
        
    except Exception as e:
        return {"fehler": f"Verbindungsfehler zur Wikipedia-API: {str(e)}"}

def vergleiche_sprachversionen(titel, ursprungs_lang, ziel_lang="en"):
    api_url = f"https://{ursprungs_lang}.wikipedia.org/w/api.php"
    params = {
        "action": "parse",
        "page": titel,
        "prop": "langlinks",
        "format": "json",
        "redirects": 1
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        res = requests.get(api_url, params=params, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if "parse" in data:
                for l in data["parse"].get("langlinks", []):
                    if l["lang"] == ziel_lang:
                        ziel_titel = l["*"]
                        vergleichs_daten = scrape_wikipedia_artikel(ziel_titel, lang=ziel_lang)
                        if "fehler" not in vergleichs_daten:
                            ew = len(vergleichs_daten['text'].split())
                            eh = len(vergleichs_daten['struktur'])
                            eq = len(vergleichs_daten['quellen']['Einzelnachweise']) + len(vergleichs_daten['quellen']['Literatur']) + len(vergleichs_daten['quellen']['Weblinks'])
                            return {
                                "titel": ziel_titel,
                                "wortanzahl": ew,
                                "anzahl_abschnitte": eh,
                                "anzahl_quellen": eq,
                                "url": vergleichs_daten["url"]
                            }
                        else:
                            return {"fehler": vergleichs_daten["fehler"]}
                return {"fehler": f"Keine Version für Sprache '{ziel_lang}' gefunden."}
    except Exception as e:
        return {"fehler": str(e)}
    return {"fehler": "Artikel konnte nicht verglichen werden."}

def generiere_zitation(titel, url, zitierstil="Harvard"):
    abrufdatum = datetime.now().strftime("%d. %B %Y")
    jahr = datetime.now().strftime("%Y")
    if zitierstil == "APA 7":
        return f"Wikipedia-Autoren. ({jahr}). {titel}. In Wikipedia. Abgerufen am {abrufdatum} von {url}"
    elif zitierstil == "MLA 9":
        return f'\"{titel}.\" Wikipedia, Die freie Enzyklopädie. {jahr}, {url}. Abgerufen am {abrufdatum}.'
    elif zitierstil == "IEEE":
        return f'Wikipedia, \"{titel},\" [Online]. Available: {url}. [Accessed: {abrufdatum}].'
    elif zitierstil == "Kurzform (Inline)":
        return f"(siehe Wikipedia-Artikel \"{titel}\", {jahr})"
    else:
        return f"Wikipedia-Autoren ({jahr}): \"{titel}\", in: Wikipedia, Die freie Enzyklopädie, URL: {url} (Abgerufen am {abrufdatum})."

def create_pdf_report(daten):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'PDFTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=24, leading=28, textColor=colors.HexColor('#1E3A8A'), spaceAfter=12
    )
    h2_style = ParagraphStyle(
        'PDFH2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=16, leading=20, textColor=colors.HexColor('#0F172A'), spaceBefore=14, spaceAfter=6
    )
    body_style = ParagraphStyle(
        'PDFBody', parent=styles['BodyText'], fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#334155'), spaceAfter=6
    )
    meta_style = ParagraphStyle(
        'PDFMeta', parent=styles['Italic'], fontName='Helvetica-Oblique', fontSize=9, leading=12, textColor=colors.HexColor('#64748B'), spaceAfter=15
    )

    story.append(Paragraph(f"WikiLens Analyse-Report: {xml_escape(daten['titel'])}", title_style))
    story.append(Paragraph(f"Generiert am {datetime.now().strftime('%d.%m.%Y %H:%M')} | Quelle: {xml_escape(daten['url'])}", meta_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#CBD5E1'), spaceBefore=5, spaceAfter=15))
    
    story.append(Paragraph("1. Zusammenfassung & Metriken", h2_style))
    worte = len(daten['text'].split())
    abschnitte = len(daten['struktur'])
    bilder_anzahl = len(daten['bilder'])
    quellen_anzahl = len(daten['quellen']['Einzelnachweise']) + len(daten['quellen']['Literatur']) + len(daten['quellen']['Weblinks'])
    
    metriken_text = f"Der Artikel umfasst <b>{worte:,}</b> Wörter, unterteilt in <b>{abschnitte}</b> hierarchische Abschnitte. " \
                    f"Es wurden insgesamt <b>{quellen_anzahl}</b> Referenzen/Quellen sowie <b>{bilder_anzahl}</b> relevante Abbildungen identifiziert."
    story.append(Paragraph(metriken_text, body_style))
    
    story.append(Paragraph("2. Inhaltsstruktur (Gliederung)", h2_style))
    if daten['struktur']:
        for sec in daten['struktur'][:30]:
            einrueckung = "&nbsp;" * (sec['ebene'] * 4)
            sec_text = f"{einrueckung}{sec['nummer']} {xml_escape(sec['name'])}"
            story.append(Paragraph(sec_text, body_style))
        if len(daten['struktur']) > 30:
            story.append(Paragraph(f"<i>... und {len(daten['struktur']) - 30} weitere Abschnitte.</i>", body_style))
    else:
        story.append(Paragraph("Keine strukturierte Gliederung vorhanden.", body_style))
        
    story.append(Paragraph("3. Verzeichnis der Quellen", h2_style))
    story.append(Paragraph(f"Einzelnachweise ({len(daten['quellen']['Einzelnachweise'])}), Fachliteratur ({len(daten['quellen']['Literatur'])}), Externe Weblinks ({len(daten['quellen']['Weblinks'])}).", body_style))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# --- STREAMLIT CONFIG & STATE ---
st.set_page_config(page_title="WikiLens", page_icon="🧠", layout="wide")

if "daten" not in st.session_state:
    st.session_state["daten"] = None
if "fehler" not in st.session_state:
    st.session_state["fehler"] = None
if "letzte_suche" not in st.session_state:
    st.session_state["letzte_suche"] = None

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Einstellungen")
    
    sprachen_namen = list(LANGUAGES.keys())
    gewaehlte_sprache_name = st.selectbox("Wikipedia-Sprachversion", options=sprachen_namen, index=0)
    st.session_state["gewaehlte_sprache"] = LANGUAGES[gewaehlte_sprache_name]
    
    st.markdown("---")
    st.subheader("📁 Kategorie-Browser")
    kat_eingabe = st.text_input("Wikipedia-Kategorie eingeben", placeholder="z. B. 'Quantenphysik'", key="kat_suchfeld")
    
    if kat_eingabe:
        kat_name_clean = kat_eingabe.strip()
        if st.session_state.get("aktuelle_kategorie") != kat_name_clean:
            st.session_state["aktuelle_kategorie"] = kat_name_clean
            st.session_state["kat_seiten_liste"] = []
            st.session_state["kat_continue_token"] = None
            
        if st.button("Seiten laden / Mehr laden"):
            mitglieder, token = lade_kategorie_mitglieder(st.session_state["aktuelle_kategorie"], fortsetzung=st.session_state.get("kat_continue_token"))
            st.session_state["kat_seiten_liste"].extend(mitglieder)
            st.session_state["kat_continue_token"] = token
            
        if st.session_state.get("kat_seiten_liste"):
            st.write(f"Gefundene Artikel ({len(st.session_state['kat_seiten_liste'])}):")
            ausgewaehlte_kat_seite = st.selectbox("Artikel aus Kategorie wählen", options=st.session_state["kat_seiten_liste"])
            if st.button("Diesen Artikel laden"):
                st.session_state["nutzer_eingabe_pending"] = ausgewaehlte_kat_seite
                st.rerun()

# --- HAUPTBEREICH ---
st.title("🧠 WikiLens")
st.markdown("Suche nach einem Thema oder füge einen direkten Wikipedia-Link ein. Die App analysiert die Architektur im Hintergrund dynamisch.")

# Dynamische Such-Fütterungsfunktion für die Live-Searchbox
def fetch_search_live(query: str):
    if not query:
        return []
    
    query_strip = query.strip()
    
    # WEICHE: Wenn der Nutzer eine vollständige URL einfügt, schlagen wir sie direkt als Option vor
    if query_strip.startswith("http://") or query_strip.startswith("https://"):
        return [query_strip]
        
    if len(query_strip) < 2:
        return []
        
    lang = st.session_state.get("gewaehlte_sprache", "de")
    return fetch_search_suggestions(query_strip, lang=lang)

# Falls eine Übernahme aus dem Kategorie-Browser vorliegt
initial_search_value = ""
if "nutzer_eingabe_pending" in st.session_state:
    initial_search_value = st.session_state.pop("nutzer_eingabe_pending")

# DAS LIVE-SUCHBOX WIDGET (Ersetzt st.text_input und den Analyse-Button vollständig!)
nutzer_eingabe = st_searchbox(
    fetch_search_live,
    default=initial_search_value,
    key="wiki_live_search",
    placeholder="Z. B. 'Albert Einstein' eintippen oder kompletten Wikipedia-Link einfügen...",
    clear_on_submit=False
)

# Automatische Trigger-Logik bei Auswahl oder Enter
if nutzer_eingabe:
    if st.session_state.get("letzte_suche") != nutzer_eingabe:
        # Alte Zustände verwerfen
        keys_to_clear = ["sprachvergleich_ergebnis", "sprachvergleich_fehler", "pdf_report_bytes", "pdf_report_url", "daten", "fehler"]
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]
        st.session_state["letzte_suche"] = nutzer_eingabe
        
        # Live-Scraping ausführen
        with st.spinner("Analysiere Wikipedia-Inhalte..."):
            ergebnis = scrape_wikipedia_artikel(nutzer_eingabe, lang=st.session_state.get("gewaehlte_sprache", "de"))
            if "fehler" in ergebnis:
                st.session_state["fehler"] = ergebnis["fehler"]
                st.session_state["daten"] = None
            else:
                st.session_state["daten"] = ergebnis
                st.session_state["fehler"] = None

# Fehler ausgeben falls vorhanden
if st.session_state.get("fehler"):
    st.error(st.session_state["fehler"])

# DATEN AUSWERTEN & REAKTIV RENDERN
daten = st.session_state.get("daten")
if daten:
    st.success(f"Analyse erfolgreich für: **{daten['titel']}** ({daten['sprache'].upper()})")
    
    if "pdf_report_bytes" not in st.session_state:
        st.session_state["pdf_report_bytes"] = create_pdf_report(daten)
        
    st.download_button(
        label="📄 PDF-Report herunterladen",
        data=st.session_state["pdf_report_bytes"],
        file_name=f"WikiLens_{daten['titel'].replace(' ', '_')}.pdf",
        mime="application/pdf"
    )
    
    tab_uebersicht, tab_struktur, tab_timeline, tab_bilder, tab_sprachen, tab_zitat = st.tabs([
        "📊 Übersicht", "🗺️ Gliederung & Mindmap", "⏳ Chronologie / Zeitleiste", "🖼️ Bildergalerie", "🌐 Sprachvergleich", "📝 Zitieren"
    ])
    
    with tab_uebersicht:
        st.subheader("Artikel-Zusammenfassung")
        wort_anzahl = len(daten['text'].split())
        abschnitt_anzahl = len(daten['struktur'])
        quellen_anzahl = len(daten['quellen']['Einzelnachweise']) + len(daten['quellen']['Literatur']) + len(daten['quellen']['Weblinks'])
        bilder_anzahl = len(daten['bilder'])
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Wortanzahl", f"{wort_anzahl:,}")
        col2.metric("Abschnitte (Gliederung)", abschnitt_anzahl)
        col3.metric("Quellen/Referenzen", quellen_anzahl)
        col4.metric("Relevante Bilder", bilder_anzahl)
        
        st.markdown(f"**Direkter Wikipedia-Link:** [{daten['url']}]({daten['url']})")
        st.markdown("### Text-Auszug (Erste 1500 Zeichen)")
        st.text_area("Vorschau", value=daten['text'][:1500] + "...", height=250, disabled=True)
        
    with tab_struktur:
        st.subheader("Inhaltsstruktur & Mindmap")
        c_links, c_rechts = st.columns([1, 2])
        
        with c_links:
            st.markdown("**Hierarchische Gliederung:**")
            if daten['struktur']:
                for sec in daten['struktur']:
                    einrueckung = "&nbsp;" * (sec['ebene'] * 4)
                    st.markdown(f"{einrueckung}**{sec['nummer']}** {sec['name']}")
            else:
                st.write("Keine Gliederung vorhanden.")
                
        with c_rechts:
            st.markdown("**Visuelle Mindmap (Top-Ebenen):**")
            dot = Digraph(comment=daten['titel'])
            dot.attr(rankdir='LR', size='12,8', bgcolor='transparent')
            dot.attr('node', shape='box', style='filled,rounded', color='#1E3A8A', fontcolor='white', fillcolor='#1E3A8A', fontname='Helvetica')
            
            haupt_knoten = textwrap.fill(daten['titel'], width=15)
            dot.node('root', haupt_knoten, fillcolor='#0F172A', color='#0F172A')
            
            letzter_knoten_auf_ebene = {0: 'root'}
            gefilterte_struktur = [s for s in daten['struktur'] if s['ebene'] <= 2]
            
            for idx, sec in enumerate(gefilterte_struktur):
                k_id = f"node_{idx}"
                kurz_name = textwrap.fill(sec['name'], width=20)
                dot.node(k_id, f"{sec['nummer']} {kurz_name}", fillcolor='#2563EB', color='#2563EB')
                
                ebene = sec['ebene']
                eltern_ebene = ebene - 1
                while eltern_ebene >= 0 and eltern_ebene not in letzter_knoten_auf_ebene:
                    eltern_ebene -= 1
                    
                vater = letzter_knoten_auf_ebene.get(eltern_ebene, 'root')
                dot.edge(vater, k_id, color='#94A3B8', penwidth='1.5')
                letzter_knoten_auf_ebene[ebene] = k_id
                
            st.graphviz_chart(dot, use_container_width=True)
            
    with tab_timeline:
        st.subheader("⏳ Vertikale Chronologie (Zeitleiste)")
        if daten['zeitleiste']:
            timeline_html = """
            <style>
            .timeline-container { position: relative; padding-left: 30px; margin-top: 20px; font-family: 'Helvetica Neue', Arial, sans-serif; }
            .timeline-container::before { content: ''; position: absolute; left: 9px; top: 5px; bottom: 5px; width: 3px; background: var(--text-color, #2563EB); opacity: 0.3; }
            .timeline-item { position: relative; margin-bottom: 25px; }
            .timeline-badge { position: absolute; left: -30px; top: 3px; width: 11px; height: 11px; border-radius: 50%; background: #2563EB; border: 2px solid var(--background-color, #fff); }
            .timeline-card { padding: 15px; border-radius: 8px; border: 1px solid rgba(128,128,128,0.2); background: rgba(128,128,128,0.05); }
            .timeline-year { font-weight: bold; color: #2563EB; font-size: 1.15rem; margin-bottom: 5px; display: block; }
            .timeline-text { font-size: 0.95rem; line-height: 1.4; }
            </style>
            <div class="timeline-container">
            """
            for item in daten['zeitleiste']:
                timeline_html += f"""
                <div class="timeline-item">
                    <div class="timeline-badge"></div>
                    <div class="timeline-card">
                        <span class="timeline-year">{xml_escape(item['jahr'])}</span>
                        <div class="timeline-text">{xml_escape(item['ereignis'])}</div>
                    </div>
                </div>
                """
            timeline_html += "</div>"
            st.markdown(timeline_html, unsafe_html=True)
        else:
            st.info("Keine eindeutigen Jahreszahlen im Fließtext für eine Zeitleiste gefunden.")
            
    with tab_bilder:
        st.subheader("Bildergalerie")
        if daten['bilder']:
            cols = st.columns(3)
            for index, img in enumerate(daten['bilder']):
                with cols[index % 3]:
                    st.image(img["url"], use_container_width=True)
                    if img["alt"]:
                        st.caption(img["alt"])
        else:
            st.write("Keine Bilder im Inhaltsbereich gefunden.")
            
    with tab_sprachen:
        st.subheader("Globaler Sprachversions-Vergleich")
        vergleichs_sprache = st.selectbox("Vergleichssprache wählen", options=["English (en)", "Français (fr)", "Español (es)", "Italiano (it)", "Nederlands (nl)", "Polski (pl)"])
        v_lang_code = vergleichs_sprache.split('(')[-1].replace(')', '')
        
        if st.button("Umfangsvergleich starten", type="secondary"):
            with st.spinner("Lade Vergleichsdaten..."):
                res_v = vergleiche_sprachversionen(daten['titel'], daten['sprache'], ziel_lang=v_lang_code)
                if "fehler" in res_v:
                    st.session_state["sprachvergleich_fehler"] = res_v["fehler"]
                    st.session_state["sprachvergleich_ergebnis"] = None
                else:
                    st.session_state["sprachvergleich_ergebnis"] = res_v
                    st.session_state["sprachvergleich_fehler"] = None
                    
        vfehler = st.session_state.get("sprachvergleich_fehler")
        vergleich = st.session_state.get("sprachvergleich_ergebnis")
        
        if vfehler:
            st.error(f"Fehler: {vfehler}")
        elif vergleich:
            ew, eh = len(daten['text'].split()), len(daten['struktur'])
            eq = len(daten['quellen']['Einzelnachweise']) + len(daten['quellen']['Literatur']) + len(daten['quellen']['Weblinks'])
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Wortanzahl", f"{ew:,}", delta=f"{ew - vergleich['wortanzahl']:,} vs. {vergleich['titel']} ({v_lang_code.upper()})")
            m2.metric("Abschnitte", eh, delta=eh - vergleich['anzahl_abschnitte'])
            m3.metric("Quellen", eq, delta=eq - vergleich['anzahl_quellen'])

    with tab_zitat:
        st.subheader("Artikel zitieren")
        zitierstile = ["Harvard", "APA 7", "MLA 9", "IEEE", "Kurzform (Inline)"]
        gewaehlter_stil = st.selectbox("Zitierformat", options=zitierstile, key="zitierstil_auswahl")
        zitat_text = generiere_zitation(daten['titel'], daten['url'], zitierstil=gewaehlter_stil)
        st.text_area("Generierte Zitation", value=zitat_text, height=100)

# --- IMPRESSUM IM FOOTER ---
st.markdown("---\n")
with st.expander("⚖️ Impressum"):
    st.markdown("""
    **Angaben gemäß § 5 TMG:**
    
    Kayra Ciftci  
    Wikimetrik  
    c/o flexdienst – #21358  
    Kurt-Schumacher-Straße 76  
    67663 Kaiserslautern  
    Deutschland  
    
    **Kontakt:** E-Mail: mein_email@domain.com  
    
    *Hinweis: Dies ist ein privates/Hobby-Entwicklerprojekt ohne kommerzielle Absichten.*
    """)