#!/home/lars/testVenv/bin/python3
"""
Sam-Welpen-Radar v2
- Nur Benachrichtigung bei NEUEN Würfen
- Schönes HTML-Mail
- SMS über smsflatrate.net
"""

# Statt individueller Züchter-Websites wird die Verbandsseite geparst:
#table_row = soup.find(
#    'tr', text=lambda t: t and 'DeinGesuchterZwinger' in t
#)
#if table_row:
#  row_hash = hashlib.sha256(str(table_row).encode('utf-8')).hexdigest()

import os
import json
import hashlib
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from urllib.parse import urlencode
from lxml import html
from lxml import etree
import time
import ssl
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, create_engine, insert, update, and_, func, select
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import sql_schema

load_dotenv()

STATE_FILE = "last_state.json"
USER_AGENT = "SamWelpenRadar/1.0 (+Privates Projekt)"

URLS = {
    "GRC aktuell": "https://www.grc.de/wp/welpen/erwartete-wuerfe/",
    "GRC geplant": "https://www.grc.de/wp/welpen/geplante-wuerfe/",
    "GRC Pedigree": "https://www.grc.pedigreedatenbank.de/wurfankuendigunglist.php",
    "DRC Welpen": "https://drc.de/interessenten/welpen/die-welpensuche/welpenlisten/gr-welpen",
    "DRC geplant": "https://drc.de/interessenten/welpen/die-welpensuche/geplante-wuerfe/gr-wurfplanungen",
}

def fetch(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    print(r.status_code)
    return r

def insert_row(row_content: str, source: str):
    # Basisklasse für unsere SQLAlchemy-Modelle
    row_hash = hashlib.sha256(row_content.encode("utf-8")).hexdigest()
    
    # SQLite-Datenbankdatei im Projektverzeichnis anlegen
    DATABASE_URL = "sqlite:///sam.db"
    engine = create_engine(DATABASE_URL, echo=False)

    with Session(engine) as session:
        try:
            # Startet eine transaktionsgesicherte Zone
            with session.begin():
                neuer_eintrag = sql_schema.Breeder(
                    content=str(row_content),
                    content_hash=str(row_hash),
                    url = source
                )
                session.add(neuer_eintrag)
                
            print("Datensatz erfolgreich transaktionssicher gespeichert!")
            
        except Exception as e:
            try:
                with Session(engine) as session:
                    try:
                        with session.begin():
                            # Statement definieren
                            stmt = (
                                update(sql_schema.Breeder)
                                .where(and_(sql_schema.Breeder.status == "neu"),
                                       sql_schema.Breeder.content_hash == str(row_hash)
                                       )
                                .values(status = "old")
                            )
                            
                            # Ausführen
                            result = session.execute(stmt)
                            print(f"{result.rowcount} Datensätze aktualisiert.")
                            
                    except Exception as e:
                        print(f"Fehler beim Bulk-Update: {e}")
            except:
                # Bei einem Fehler wird hier automatisch das Rollback abgefangen
                print(f"Transaktion fehlgeschlagen, Rollback ausgeführt: {e}. Versuche update")




def extract_entries(htmla: str, source: str) -> list[str]:
    """
    Extrahiert grobe, aber stabile Einträge (Züchter + Datum etc.).
    Wir speichern die Einträge als Strings und vergleichen später.
    """

    lines = []

    soup = BeautifulSoup(htmla.content, 'html.parser')
    wurfliste_tables = soup.find_all("table")
    for wurfliste_table in wurfliste_tables:
        if "drc-wurfliste__table" in wurfliste_table.get("class"):
            for row in wurfliste_table.tbody.find_all("tr"):
                insert_row(str(row.text), source)
                if "drc-wurfliste__row" in row.get("class"):
                    cols = row.find_all("td")
                    lines.append(str(cols[0].strong.text) + '->' + str(cols[5].text))
                else:
                    cols = row.find_all("td")
                    lines.append(str(cols[0].text)) 
        elif "drc-wurfplanung__table" in wurfliste_table.get("class"):
            for row in wurfliste_table.tbody.find_all("tr"):
                insert_row(str(row.text), source)
                cols = row.find_all("td")
                lines.append(str(cols[0].strong.text) + '->' + str(cols[5].text))
        elif "tablepress-id-51" in wurfliste_table.get("class") or "tablepress-id-49" in wurfliste_table.get("class"):
            for row in wurfliste_table.tbody.find_all("tr"):
                insert_row(str(row.text), source)
                cols = row.find_all("td")
                try:
                    lines.append(str(cols[0].strong.text) + '->' + str(cols[5].text))
                except:
                    lines.append(str(cols[0].text))
        elif "tbl_wurfankuendigunglist" in wurfliste_table.get("id"):
            for row in wurfliste_table.tbody.find_all("tr"):
                insert_row(str(row.text), source)
                cols = row.find_all("td")
                lines.append(str(cols[2].find("a").text) + "->" + str(cols[2].find("span").text) )


    return sorted(list(set(lines)))
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def send_email_html(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["From"] = os.getenv("SENDER_EMAIL")
    msg["To"] = os.getenv("EMAIL_TO")
    msg["Subject"] = subject

    # Plaintext-Fallback
    plain = BeautifulSoup(html_body, "lxml").get_text()
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context() 
    with smtplib.SMTP_SSL(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT")), context=context) as server:
        server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
        server.sendmail(os.getenv("SENDER_EMAIL"), os.getenv("EMAIL_TO"), msg.as_string())
    print("HTML-E-Mail gesendet.")

def send_sms_flatrate(text: str):
    """SMS über smsflatrate.net"""
    params = {
        "key": os.getenv("SMSFLATRATE_KEY"),
        "from": os.getenv("SMS_FROM", "SamWelpenRadar"),
        "to": os.getenv("SMS_TO"),
        "text": text[:1000],
        "type": "auto",          # oder 1 / 10 je nach deinem Tarif
    }
    url = "https://www.smsflatrate.net/schnittstelle.php?" + urlencode(params)
    r = requests.get(url, timeout=15)
    print(f"SMS-Antwort: {r.text}")
    if not r.text.startswith("100"):
        raise Exception(f"SMS-Fehler: {r.text}")

def build_html(new_entries: dict) -> str:
    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5; color: #333;">
        <div style="max-width: 700px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #d4a017;">🐶 Sam-Radar – Neue Golden Retriever Würfe</h2>
            <p style="color: #666;">{datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr</p>
    """

    for source, entries in new_entries.items():
        html += f"""
            <div style="margin: 30px 0; padding: 15px; background: #f9f5e9; border-left: 5px solid #d4a017; border-radius: 6px;">
                <h3 style="margin-top: 0; color: #8B6914;">{source}</h3>
                <ul style="padding-left: 20px;">
        """
        for entry in list(entries):
            # Einträge etwas lesbarer machen
            short = str(entry[:280]) + ("..." if len(entry) > 280 else "")
            html += f"<li style='margin-bottom: 12px;'>{short}</li>"
        html += """
                </ul>
            </div>
        """

    html += """
            <p style="margin-top: 40px; font-size: 14px; color: #888;">
                Viel Erfolg bei der Suche nach Sam! 💛<br>
                <small>Sam-Welpen-Radar</small>
            </p>
        </div>
    </body>
    </html>
    """
    return html

def main():
    # SQLite-Datenbankdatei im Projektverzeichnis anlegen
    DATABASE_URL = "sqlite:///sam.db"
    engine = create_engine(DATABASE_URL, echo=False)

    time.sleep(10)
    print(f"[{datetime.now()}] Sam-Radar v2 startet...")
    #old_state = load_state()
    new_state = {}
    newly_found = {}
    brand_new = {}
    entries = {}
    entries_all = 0

    for name, url in URLS.items():
        try:
            html = fetch(url)
            extract_entries(html, name)
            with Session(engine) as session:
                new_state[name] = session.execute(select(sql_schema.Breeder.content).select_from(sql_schema.Breeder).where(and_(sql_schema.Breeder.status == "neu"), sql_schema.Breeder.url == name)).all()

            entries[name] = len(set(new_state.get(name, [])))
            entries_all = len(set(new_state.get(name, []))) + entries_all
            #brand_new = current_entries - old_entries
            
            if new_state and entries[name] > 0:
                newly_found[name] = set(new_state.get(name, []))
                print(f"→ {entries[name]} neue Einträge bei {name}")
            else:
                print(f"→ keine neuen Einträge bei {name}")

        except Exception as e:
            print(f"Fehler bei {name}: {e}")

    #save_state(new_state)

    if newly_found:
        subject = f"🐶 Sam-Radar: {sum(len(v) for k, v in newly_found.items())} neue Golden-Retriever-Würfe"
        html_body = build_html(newly_found)

        try:
            send_email_html(subject, html_body)
        except Exception as e:
            print(f"E-Mail Fehler: {e}")

        #try:
        #    sms_text = f"Sam-Radar: {sum(len(v) for v in newly_found.values())} neue Würfe gefunden! Details in deiner Mail."
        #    send_sms_flatrate(sms_text)
        #except Exception as e:
        #    print(f"SMS Fehler: {e}")
    else:
        print("Keine neuen Würfe gefunden.")

if __name__ == "__main__":
    main()