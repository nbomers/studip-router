# Problemliste und Verbesserungsbedarf

Stand: 6. November 2026. Geprüfter Ausgangsstand: `5e52eb8`, Version 0.4.2.

Diese Liste erweitert die angefragten Bereiche UI/Design, überladene Ansichten, Konfiguration, Wortlisten, Dokumentation, lange Pfade, Hilfestellungen, Edgecases und Sicherheit. Ergänzt sind Datenintegrität, Wiederherstellung, Barrierefreiheit, Performance, Hintergrundbetrieb, Plattformverhalten und Wartbarkeit.

Die ursprünglichen Befunde unten bleiben als Ausgangsbefund erhalten. Die
Bearbeitung steht in der folgenden Tabelle. Anwendungscode, Tests, CI-Prüfungen
und Dokumentation wurden geändert; es wurde nichts veröffentlicht oder deployed.

**43 Punkte sind umgesetzt bzw. mit lokalen Prüfungen bearbeitet. Zwei Punkte
(33 und 36) sind nur teilweise verifiziert:** reale macOS-/Cloud-Dateisysteme und
Screenreader stehen hier nicht zur Verfügung. Die Sicherheitsprüfpunkte wurden
gezielt geprüft und gehärtet; ein formaler vollständiger Codex-Security-Scan bleibt
zusätzlich wegen fehlender Plugin-Referenzen und Scan-Werkzeuge nicht möglich.
Das ist keine Zusicherung vollständiger Fehler- oder Schwachstellenfreiheit.

## Bearbeitungsstand aller 45 Punkte

| Nr. | Ergebnis | Umsetzung / Beleg |
|---|---|---|
| 01 | Umgesetzt | Falsche Kurstabellentypen erzeugen `ConfigError`; parametrisierte Regressionstests. |
| 02 | Umgesetzt | Leere/blanke Serverfelder werden zurückgewiesen; URL-Fehler werden verständlich erklärt. |
| 03 | Umgesetzt | Relative Root-Pfade sind relativ zur Config; Test wechselt das Arbeitsverzeichnis. |
| 04 | Umgesetzt | Validierung und Verwendung nutzen denselben bereinigten Root-Wert. |
| 05 | Umgesetzt | Nicht endliche Größenlimits in Config und Regeln abgelehnt. |
| 06 | Umgesetzt | Dialoge passen bei 40×20, 80×24 und nach Resize; Geometrietests. |
| 07 | Umgesetzt | Lokale und entfernte Synonyme werden zusammengeführt. |
| 08 | Umgesetzt | Eigene Begriffe gelten auch bei der lokalen Zielsuche und bei Vorschlägen. |
| 09 | Umgesetzt | Lokale False Friends werden ausgeschlossen; fünf konkrete Regressionen. |
| 10 | Umgesetzt | Alle Ordnerebenen werden geprüft; Elternpriorität bleibt stabil. |
| 11 | Umgesetzt | Terminprüfung nutzt dieselbe Klassifikation; `Examples` ist keine Prüfung. |
| 12 | Umgesetzt | `Midterm`, `Testat`, `Prufung`, `Quiz` erkannt. |
| 13 | Umgesetzt | Formulare, Hilfe, Report und Dialoge haben responsive Maximalbreiten und Scrollverhalten. |
| 14 | Umgesetzt | Vollständige Details und Kopieren mit `d`/Ctrl+Y; Kürzung erhält beide Enden und berücksichtigt Zellenbreiten. |
| 15 | Umgesetzt | Unter 70 Spalten wechselt `v` zwischen Nachrichtenliste und Inhalt; Resize-Test. |
| 16 | Umgesetzt | Letzte Config-Einstellung mit `u` rückgängig; Bestätigung und Änderungserkennung. |
| 17 | Umgesetzt | Bruchteilige MB-Werte bleiben erhalten; Enter öffnet freie Eingabe; Test mit 12,75 MB. |
| 18 | Umgesetzt | CLI und TUI verwenden vollständige gemeinsame Match-Zusammenfassung, einschließlich JSON-Ausgabe. |
| 19 | Umgesetzt | TUI-Regeltest zeigt und bearbeitet Größe, MIME, Datum, Beschreibung und Ordnertyp. |
| 20 | Umgesetzt | Vorschaufilter plus 500 Zeilen pro Seite; vollständiger Syncplan bleibt erhalten. |
| 21 | Bearbeitet | Englische UI/Referenz, DE-/EN-Wortlisten, zweisprachiges Glossar und explizite Sprachkonvention. |
| 22 | Umgesetzt | Settings-Beschreibung entspricht Essentials/Advanced und den tatsächlich verfügbaren Optionen. |
| 23 | Umgesetzt | README auf 255 Zeilen reduziert; Nutzung, Config und Fehlerhilfe separat verlinkt. |
| 24 | Umgesetzt | Sortierprioritäten, Simulationswerte und Grenzen in `USABILITY.md` erklärt. |
| 25 | Umgesetzt | Gesamtpfad-/Komponentenbudget inklusive Reserve; Planner, Executor und Doctor prüfen. |
| 26 | Umgesetzt | Scheduler bindet Config/State-Pfade; Jobdateien enthalten kein Passwort; Tests für Sonderzeichen. |
| 27 | Umgesetzt | Config-Lock, eindeutige temporäre Dateien und Erkennung externer Änderungen; Konkurrenztests. |
| 28 | Umgesetzt | Gemeinsamer privater Writer mit Datei-fsync und Verzeichnis-fsync; Abbruchtests. |
| 29 | Geprüft/gehärtet | ENOSPC/EDQUOT/EROFS und DB-Ausfall simuliert; Original bleibt, nächster Lauf adoptiert vollständigen Download. |
| 30 | Geprüft/gehärtet | Move-Intent vor Dateiverschiebung; Undo von Teilabläufen und zwei Crash-Zeitfenstern getestet. Mehrdeutige Inhalte erfordern manuelle Prüfung. |
| 31 | Umgesetzt/gemessen | SQL-Zähler statt Laden aller Records; lokale Routingprüfung und Updatehinweis im Hintergrund. 50k-Messung liegt bei. |
| 32 | Geprüft/verbessert | Offline-Benchmark mit 1k/10k/50k Dateien, paginierte Vorschau und vorhandene Pagination-/Abbruchtests. Keine echte Netzwerkbenchmark-Messung. |
| 33 | Teilweise verifiziert | Linux-, Unicode-, Kollisions- und Placeholder-Simulationen geprüft; reale APFS/iCloud/OneDrive/Netzlaufwerke hier nicht verfügbar. Bestehende macOS-CI nicht remote gestartet. |
| 34 | Umgesetzt/geprüft | DST-Vergleich auf tatsächliche Zeitpunkte umgestellt; Regression für doppelte Stunde und Mitternachtsverschiebung. |
| 35 | Umgesetzt | CLI auf Kontext/Accounts/Regeln/Services verteilt; TUI nach Abläufen aufgeteilt. Öffentliche Screen-Imports bleiben kompatibel. |
| 36 | Teilweise verifiziert | Tastatur, Filter, Undo, Clipboard-Übergabe, Unicode-Zellen und Resize headless getestet; reale Screenreader und alle Emulator-/Theme-Kombinationen extern offen. |
| 37 | Bearbeitet | Quiz und Lernkarten ergänzt; Daten/Software/Medien bewusst kursabhängigen Regeln überlassen, Entscheidung dokumentiert. |
| 38 | Geprüft/gehärtet | Bestehende Auth-/Fehlertests plus unveränderte Passwort-Leerzeichen im Setup; keine Secrets in Jobdateien. Reale OS-Keychains nicht live getestet. |
| 39 | Geprüft/gehärtet | Fremde direkte API-Origins und ungültige URLs abgelehnt; HTTPS-CDN-Redirects ohne Zugangsdaten bleiben funktionsfähig. |
| 40 | Geprüft/gehärtet | Unsicheren Rename-Fallback entfernt, Symlink-Ausbrüche geprüft, Archive-Grenzen getestet. Lokaler gleichzeitiger Austausch von Elternordnern bleibt außerhalb des abgesicherten Modells. |
| 41 | Geprüft/gehärtet | Merge-Neuziele ohne Überschreiben; autorisierte Hash-Basis bleibt stabil; erneute Prüfung bei Merge/Unlock; Datenintegritäts- und Undo-Tests. Letztes lokales Check/Replace-Race dokumentiert. |
| 42 | Geprüft/gehärtet | Programme/Shortcuts bleiben gesperrt; unbekannte Dateitypen benötigen eine Entscheidung; Opener-Fehler werden angezeigt. |
| 43 | Geprüft/gehärtet | Markup-/Controltests bestehen; Bidi-Filter für API-/Reporttexte erweitert; Details interpretieren kein Markup. |
| 44 | Geprüft/gehärtet | JSON-Limit 16 MiB, kontrollierte Transportfehler/Retry, Pagination-/Download-/ZIP-Grenzen; pypdf aktualisiert. Regex/Viewer/PDF-Parser bleiben vertrauensabhängige Grenzen. |
| 45 | Geprüft/umgesetzt | Privater Config-Writer inkl. Backups, Releaseberechtigungen geprüft, Dependency-Advisories beseitigt; Wiederholungsprüfung mit Hashes in CI. |

### Nachweise und verbleibende Grenzen

- [Sicherheits-/Zuverlässigkeitsprüfung](SECURITY_REVIEW.md), einschließlich unabhängiger Vor- und Nachprüfung der konkreten Befunde.
- [Dependency-Audit](dependency-audit.json): keine bekannten Advisories nach Update auf pypdf 6.21.4 und certifi 2026.10.23; 31 Export-Einträge.
- [Performance-Messung](performance-audit.json): 50.000 unveränderte Dateien in ca. 5,75 s geplant, ca. 1,89 s für die Umorganisationsprüfung, SQL-Zähler ca. 4 ms. Synthetische lokale Daten, keine Live-API.
- Neue Regressionen in `tests/test_audit_followup.py` und `tests/test_audit_ui.py`; bestehende Tests bleiben erhalten. Ein Kürzungs-Assertion wurde an die neue Darstellung mit beiden Textenden angepasst, bei unveränderter Prüfung auf wörtliches Markup.
- Abschließender Gesamtlauf: **678 Tests bestanden in 42,90 s** (Python 3.12/Linux, Null-Keyring; Netzwerk im Testprozess blockiert).
- `ruff check .`, `ruff format --check .` (100 Dateien), `mypy` (50 Quelldateien) und `git diff --check`: erfolgreich.
- Wheel und Quelldistribution erfolgreich nach `/tmp/studip-audit-dist` gebaut. Isolierter CLI-Start aus dem gebauten Wheel (`--version` und `rules --help`) erfolgreich.
- Lokale Markdown-Dateilinks geprüft: keine fehlenden Zieldateien.

Der Security-Skill verlangt: „Start source review and launch scan workers only after preflight returns `ready`.“ Die Referenz `../../references/scan-prologue.md` des [geladenen Skills](skill://Plugin_1e648473be9c8191a91ac3947151af55/security-scan/SKILL.md) war nicht abrufbar. Deshalb wird kein vollständiger Scan nach diesem Verfahren behauptet. Konkrete Sicherheitsänderungen wurden gezielt reproduziert, nachgeprüft und getestet; beim Fix-Skill war ebenfalls die Artefakt-Referenz nicht abrufbar. Es wurden nur vom Nutzer angeforderte Berichte im Repository gespeichert.

## Ursprüngliche Bestandsaufnahme (vor den Änderungen)

## Bedeutung

- **B – bestätigt:** durch einen gezielten lokalen Aufruf oder eine Layoutmessung reproduziert.
- **V – Verbesserung:** am Code belegbare Einschränkung oder Designbewertung; nicht automatisch ein Fehler.
- **O – offen:** sinnvoller Prüfauftrag; Fehler oder Ausnutzbarkeit noch nicht nachgewiesen.
- **P1:** zuerst bearbeiten, weil Start, Konfiguration oder Kernfunktion betroffen sind.
- **P2:** regulärer Verbesserungsbedarf.
- **P3:** Komfort, Ausbau oder Wartbarkeit.

Prioritäten bewerten hier die Bearbeitungsreihenfolge, nicht die Severity einer Sicherheitslücke. Für offene Punkte ist die Priorität eine Prüfpriorität.

## 1. Bestätigte Probleme

| Nr. | Prio | Problem und Nachweis | Verbesserung | Fundstelle |
|---|---|---|---|---|
| 01 | P1 | `courses = 42` erzeugt `TypeError: 'int' object is not iterable` statt einer verständlichen `ConfigError`. Die erlaubten Keys werden mit `set(...)` gebildet, bevor der Tabellen-Typ validiert wird. | Typ zuerst validieren; auch Zahlen, Boolean und andere ungültige Tabellenwerte abdecken. | `config.py`, `parse`, Aufbau der Kurstabelle |
| 02 | P1 | `base_url = ''` und `username = ''` werden als gültige Konfiguration akzeptiert. Pflichtfeldprüfung unterscheidet nur zwischen vorhanden und fehlend. | Leere und ausschließlich aus Leerzeichen bestehende Server-/Benutzerangaben mit konkreter Hilfe zurückweisen. | `config.py`, `parse` |
| 03 | P1 | `sync.root = 'uni'` wird akzeptiert. Der Pfad hängt damit vom Arbeitsverzeichnis ab und kann im Hintergrundbetrieb anders aufgelöst werden als beim manuellen Start. Die abweichende Ausführung selbst wurde nicht live getestet. | Absolute Pfade verlangen oder relative Pfade dauerhaft relativ zur Konfigurationsdatei auflösen. | `config.py`, `parse`; `schedule.py`, erzeugte Jobs |
| 04 | P2 | Bei `sync.root = ' /tmp/uni '` wird `/tmp/uni` geprüft, aber anschließend `Path(' /tmp/uni ')` gespeichert. Das Ergebnis ist ein anderer, relativer Pfad. | Einmal normalisieren und exakt diesen Wert validieren und verwenden. | `config.py`, Root-Validierung und `Config(root=...)` |
| 05 | P1 | TOML-Größenlimits `nan` und `inf` werden akzeptiert. Die Settings verwenden `int(c.exclude.max_size_mb or 0)`, was für diese Werte nicht funktioniert. | Mit `math.isfinite` validieren; Wertebereich und Darstellung vereinheitlichen. | `config.py`, `_Validator.exclude`; `tui/settings.py`, Einstellung `size limit` |
| 06 | P2 | Ein Textdialog ist auch bei einem Terminal mit 40×20 Zellen 72 Spalten breit; das Eingabefeld misst 66 Spalten. Bei 80×24 passt derselbe Dialog. | Dialogbreite an den verfügbaren Platz begrenzen; schmale Ansichten und lange Inhalte scrollfähig gestalten. | `tui/app.py`, `.dialog`; gemessen mit `App.run_test` und `TextScreen` |
| 07 | P2 | `Präsentationen`, `Handouts` und `Zusammenfassungen` werden remote als `slides` erkannt, lokal aber nicht als Ziel für `slides`. | Lokale und entfernte Synonyme konsistent halten oder Unterschiede bewusst dokumentieren. | `suggest.py`, `REMOTE_KEYWORDS`, `LOCAL_KEYWORDS`, `local_category_dirs` |
| 08 | P2 | Eigene Wörter gelten nicht gleichermaßen für lokale Ziele: `auto_route('Werkstatt', '', 'x.pdf', ['Werkstatt'], {'exercises': ('werkstatt',)})` liefert `None`. | Eigene Wörter auch der Zielsuche übergeben oder eine explizite Kategorie-zu-Ziel-Zuordnung anbieten. | `suggest.py`, `auto_route`, `_local_target`, `local_category_dirs` |
| 09 | P2 | Lokale `Wallpapers` werden als Literaturziel erkannt; `Cheat sheets`, `Datasheets`, `Merkblätter` und `Deckblätter` als Übungsziele. Die remote vorhandenen False-Friend-Filter fehlen in der lokalen Erkennung. | False-Friend-Behandlung auf beiden Seiten verwenden. | `suggest.py`, `_FALSE_FRIENDS`, `_category`, `local_category_dirs` |
| 10 | P2 | `Material/Woche 1/Dateien/Übungen` erhält keine Ordnerkategorie: nur die ersten drei Ebenen werden geprüft. Ein Dateiname kann diesen konkreten Fall teilweise auffangen, die Ordnerinformation selbst geht verloren. | Tiefe gezielt erweitern oder die nächsten aussagekräftigen Ebenen auswerten; Eltern-/Kindpriorität festlegen. | `suggest.py`, `folder_category` |
| 11 | P2 | Ein Termin mit Kategorie `Examples` gilt als Prüfung, weil `exam` als Teilzeichenfolge genügt. | Wortgrenzen und False Friends berücksichtigen; Dateirouting und Terminlogik nicht mit unterschiedlichen Regeln pflegen. | `models.py`, `EXAM_WORDS`, `CourseDate.is_exam` |
| 12 | P2 | Terminkategorien `Midterm`, `Testat` und `Prufung` gelten nicht als Prüfung, obwohl die Dateisortierung entsprechende Begriffe teilweise kennt. | Prüfungsvokabular vereinheitlichen; mehrdeutige Begriffe wie `Quiz` bewusst behandeln. | `models.py`, `EXAM_WORDS`; `suggest.py`, Prüfungswörter |

## 2. UI, Design, Hilfestellung und Dokumentation

Diese Punkte sind Designbewertungen und belegte Einschränkungen (**V**), keine behaupteten Abstürze.

| Nr. | Prio | Verbesserungsbedarf | Konkreter Ansatz | Fundstelle |
|---|---|---|---|---|
| 13 | P2 | Feste Breiten von 80 für Formulareingaben, 76 für Hilfe und 90 für Reports erschweren kleinere Terminals. | Prozentuale Maximalbreiten, Mindestgröße anzeigen, bei Bedarf vertikal umbrechen. | `tui/app.py`, `.form Input`, `.help`, `.report` |
| 14 | P2 | Die Syncvorschau kürzt Kursnamen auf 24 sowie Quell- und Zielpfade auf 40 Zeichen. Ähnliche Namen werden schwer unterscheidbar. | Detailbereich mit vollständigem Pfad und Kopieraktion; beim Kürzen Dateiendung und Ende des Pfads erhalten. | `tui/views.py`, `SyncView.show_plan`, `clip` |
| 15 | P2 | Die Nachrichtenansicht verwendet dauerhaft 45 % der Breite für die Liste. | Bei wenig Platz zwischen Liste und Inhalt umschalten; Breite bei großen Terminals anpassbar machen. | `tui/app.py`, `#news-list`; `tui/views.py`, `NewsView.compose` |
| 16 | P2 | Einstellungen werden beim Durchschalten sofort gespeichert. Eine Änderungshistorie oder Rücknahme pro Einstellung fehlt in diesem Ablauf. | Letzte Änderung zurücknehmen und gespeicherten Zustand kurz bestätigen; sofortiges Speichern gut sichtbar erklären. | `tui/settings.py`, `SettingRow.action_move`, `SettingsScreen._save` |
| 17 | P2 | Eigene Größenlimits werden über `int(...)` angezeigt; Bruchteile verlieren in der UI ihre Genauigkeit. Die Auswahl enthält nur feste Werte. | Benutzerdefinierte Werte unverändert anzeigen und direkt editierbar machen. | `tui/settings.py`, Einstellung `size limit` |
| 18 | P2 | „Your rules“ zeigt nur einen Teil möglicher Matchbedingungen. Eine ausschließlich über MIME, Größe, Datum oder Beschreibung eingeschränkte Regel kann als `everything` erscheinen. | Alle wirksamen Bedingungen und First-Match-Priorität darstellen. | `tui/views.py`, `RulesView.render_rule_list`; `rules.py` |
| 19 | P2 | Der Regeltest simuliert immer 100.000 Bytes, aktuelle Änderungszeit, `application/octet-stream` und leere Beschreibung. Ergebnisse können vom realen Dokument abweichen. | Simulationswerte sichtbar machen und editierbar anbieten; alternativ echte Datei aus Vorschau auswählen. | `tui/views.py`, `RulesView.test_changed` |
| 20 | P3 | Die Hauptnavigation ist bereits mit fünf Aktionen und „More“ reduziert; Einstellungen haben Essentials/Advanced. Pauschales weiteres Verstecken würde Auffindbarkeit verschlechtern. | Überladung gezielt an langen Vorschauen, Formularen und Regellisten messen; Suche, Kursfilter und Details nach Bedarf ergänzen. | `tui/app.py`, `PRIMARY`, `HomeScreen`; `tui/settings.py`, `ESSENTIALS` |
| 21 | P3 | UI und Hauptdokumentation sind englisch, die wichtigsten fachlichen Ordnerbegriffe oft deutsch. | Sprachstrategie festlegen; Begriffe, Fehlermeldungen und Hilfe konsistent übersetzen oder bewusst zweisprachig erklären. | `README.md`; `tui/`; `suggest.py` |
| 22 | P2 | Die Featuretabelle verspricht „every option on one page“. Die aktuelle Settingsansicht zeigt Essentials und eingeklappte Advanced-Einstellungen; zahlreiche Kurs-/Regeloptionen erfordern weiterhin die Config. | Den tatsächlichen Umfang der Settings präzise beschreiben und auf Spezialoptionen verlinken. | `README.md`, Features; `tui/settings.py` |
| 23 | P3 | Die README bündelt Installation, vollständige Konfiguration, Referenz, Sicherheit und Fehlerhilfe in über 700 Zeilen. | Kurze Einstiegshilfe behalten; Konfigurationsreferenz, Workflows und Troubleshooting in eigene verlinkte Dokumente auslagern. | `README.md` |
| 24 | P2 | Der Test „Test a file name“ erklärt seine künstlichen Metadaten nicht. Die Wortlistenlogik hat außerdem unterschiedliche Remote-/Local-/Terminregeln. | Grenzen der Vorschau und Sortierprioritäten mit konkreten Beispielen dokumentieren. | `README.md`, Konfiguration; `tui/views.py`, `RulesView`; `suggest.py`; `models.py` |
| 25 | P2 | Lange Pfade werden auf Komponentenebene begrenzt, eine zentrale Prüfung der gesamten Pfadlänge und eine passende Doctor-Hilfe sind in den geprüften Pfadhelfern nicht vorhanden. Das ist kein Nachweis, dass ein regulärer Sync bereits scheitert. | Vorab gesamtes Ziel samt temporären Suffixen prüfen; bei Bedarf Verkürzung anbieten und Originalnamen nachvollziehbar halten. | `fsutil.py`, `MAX_NAME_BYTES`, `truncate_name`, `part_path`; `doctor.py`, `_check_paths` |

## 3. Ergänzte Bereiche: Zuverlässigkeit, Performance und Wartbarkeit

**V** kennzeichnet Codeeinschränkungen, **O** noch zu prüfende Szenarien.

| Nr. | Status | Prio | Prüfpunkt oder Verbesserung | Vorgehen / Abnahmekriterium |
|---|---|---|---|---|
| 26 | V | P1 | Background-Jobs übernehmen die Pfad-Overrides des aufrufenden Prozesses nicht explizit. | `STUDIP_ROUTER_CONFIG`, `STUDIP_ROUTER_STATE_DIR`, XDG-Pfade und Profil im Job eindeutig binden oder ihre Grenzen erklären. Quellen: `schedule.py`, `launchd_plist`, `systemd_units`; `paths.py`. |
| 27 | O | P1 | Gleichzeitige Konfigurationsänderungen können sich überschreiben; beide Writer verwenden einen festen `.tmp`-Namen und getrenntes Lesen/Schreiben. Ein tatsächlicher Parallelverlust wurde nicht reproduziert. | Zwei Instanzen und externen Editor testen; Sperre oder Änderungserkennung sowie eindeutige temporäre Dateien verwenden. Quellen: `config_edit.py`, `_save`; `tomlwrite.py`, `append_blocks`. |
| 28 | V | P2 | Config-Writer ersetzen atomar, verwenden aber nicht die `fsync`-Absicherung der Download-Writer. | Haltbarkeit bei Prozessabbruch/Stromausfall bewusst festlegen; gemeinsame Schreibprimitive prüfen. Quellen: `config_edit.py`, `tomlwrite.py`, `fsutil.py`. |
| 29 | O | P1 | Speicher voll, Quota erschöpft, schreibgeschützte Datenträger und Abbruch zwischen Dateischreib- und DB-Schritt. | Fehlersimulation: Benutzerdateien bleiben erhalten, temporäre Dateien werden aufgeräumt und der nächste Lauf stellt Konsistenz wieder her. |
| 30 | O | P1 | Rücknahme und Wiederanlauf bei teilweise ausgeführtem Reorganize, Semesterwechsel und Merge. | Mehrere Dateien, Zielkonflikte und Absturz nach Teilschritten testen; Rücknahme darf manuell geänderte Dateien nicht ersetzen. |
| 31 | V | P2 | Die Startseite ruft `pending_moves` synchron aus der Statusaktualisierung auf; die Planung kann viele lokale Dateien untersuchen. Eine spürbare Verzögerung wurde nicht gemessen. | Mit 1.000/10.000/50.000 Dateien messen; bei Bedarf Worker und Cache einsetzen. Quellen: `tui/app.py`, `HomeScreen.refresh_status`; `reorganize.py`. |
| 32 | O | P2 | Große Datenmengen, tiefe Ordner und langsame Cloud-/Netzlaufwerke. | Zeit, Speicher, Requestanzahl und Bedienbarkeit messen; Filter, inkrementelle Darstellung und Abbruch testen. |
| 33 | O | P2 | Plattformunterschiede: Groß-/Kleinschreibung, Unicode-Normalisierung, iCloud-Platzhalter, OneDrive und Netzlaufwerke. | Dateikollisionen und Wiedererkennung auf Linux und macOS mit echten Dateisystemeigenschaften testen. Windows ist laut README nicht unterstützt und zählt nicht pauschal als Produktfehler. |
| 34 | O | P2 | Zeitzonen, Sommerzeitwechsel, Terminverschiebungen über Mitternacht und fehlende Offsets. | Mit Europe/Berlin, UTC und DST-Grenzen prüfen; keine doppelten, falschen oder verlorenen Terminmeldungen. |
| 35 | V | P3 | Große Module erschweren gezielte Änderungen: `tui/views.py` hat 2.115 und `cli.py` 1.643 Zeilen. | Nach Benutzerabläufen aufteilen und gemeinsame Fachlogik von CLI/TUI wiederverwenden; Länge allein ist kein Funktionsfehler. |
| 36 | O | P2 | Barrierefreiheit und visuelle Regressionen. | Fokusführung, Tastaturbedienung, Screenreader/Terminal-Kompatibilität, Kontraste aller Themes, Farbunabhängigkeit und Unicode-Zellbreiten prüfen. Die Suite enthält bereits einen 80×24-Settingstest; weitere Dialog-/Resizefälle ergänzen. |
| 37 | O | P2 | Ausbau der Wortlisten und Kategorien. | `Quiz/Quizzes`, Lernkarten/Flashcards, Datensätze, Software und Medien anhand realer Beispiele bewerten. Fehlende Spezialkategorien sind Produktentscheidungen, nicht automatisch Bugs. Kursabhängige Bedeutung und Mehrdeutigkeiten priorisieren. |

## 4. Sicherheit: offene Prüfaufträge

Alle acht Punkte haben Status **O**. Bereits vorhandene Schutzmaßnahmen und Tests müssen mitgeprüft werden; ihre Existenz ist weder ein Schwachstellenbeweis noch eine Garantie. Keine Live-Zugangsdaten und kein produktiver Stud.IP-Server wurden verwendet.

| Nr. | Prüfprio | Prüfbereich | Nachzuweisende Eigenschaft |
|---|---|---|---|
| 38 | P1 | Zugangsdaten, Keyring, Headless-Variablen, Fehlerausgaben und Crashreports | Keine Passwörter in Logs, Config, SQLite oder Fehlermeldungen; klare Behandlung gesperrter/fehlender Keyrings. |
| 39 | P1 | HTTPS, Redirects, API-Links und Downloadziele | Zugangsdaten bleiben beim konfigurierten Origin; keine unzulässigen Protokollwechsel oder durch Antworten ausgelösten beliebigen Netzwerkziele. |
| 40 | P1 | Dateipfade, Symlinks, Archive und Race Conditions | Keine Schreibzugriffe außerhalb der erlaubten Ziele, auch bei Zieländerungen zwischen Prüfung und Zugriff; ZIP-Limits und Pfadprüfungen überprüfen. |
| 41 | P1 | Überschreiben, Adoption, Versionsbildung und Undo | Lokale Bearbeitungen, vorhandene Fremddateien und parallel angelegte Dateien bleiben geschützt; Integritätsprüfung und Wiederherstellung nachvollziehbar. |
| 42 | P1 | Heruntergeladene Dateien öffnen/vorschauen | Skripte, Programme und plattformspezifische Starter werden nicht unbemerkt ausgeführt; auch doppelte Endungen und Dateizuordnungen prüfen. |
| 43 | P2 | Fremde Texte und Metadaten | Keine Terminal-Escapes, Markup-Injektion, irreführenden Steuerzeichen oder gefährlichen Links aus Titeln, Dateinamen, Nachrichten und Reports. |
| 44 | P2 | Ressourcenverbrauch | Grenzen für Pagination, Antwort-/Downloadgrößen, Kompression, PDFs und Regex-Laufzeit überprüfen; langsame oder fehlerhafte Gegenstellen dürfen UI und Rechner nicht unbegrenzt blockieren. |
| 45 | P2 | Privatsphäre, Dependencies und Releaseprozess | Dateirechte inklusive Backups/temporären Dateien, datensparsame Fehlerberichte, Opt-in-Updateprüfung, bekannte Dependency-Advisories und Releaseberechtigungen prüfen. Versionsgebundene Advisoryprüfung steht aus. |

## Validierung der ursprünglichen Bestandsaufnahme

- Gezielte lokale Reproduktionen: Config-Fälle 01–05, Wortlistenfälle 07–10, Terminbegriffe 11–12.
- Headless-UI-Lauf mit Fake-Stud.IP: Textdialog bei 80×24 und 40×20; Punkt 06 anhand der Widget-Geometrie bestätigt. Kein umfassender visueller Usabilitytest.
- `ruff check .`: erfolgreich.
- `ruff format --check .`: erfolgreich, 78 Dateien bereits formatiert.
- `mypy`: erfolgreich, 39 Quelldateien.
- Gezielte bestehende Tests für Konfiguration, Config-Editing, Wortlisten, Vorschläge, Termine, Dateiprobleme und Beispielkonfiguration: **233 bestanden in 0,72 s**.
- Gesamte Pytest-Suite: **kein vollständiger erfolgreicher Lauf**. Der erste Lauf wurde nach einem Stillstand in der TUI-Testgruppe unterbrochen (85 bestanden in 236,27 s). Ein Wiederholungsversuch mit dem in CI verwendeten Null-Keyring zeigte denselben Stillstand und wurde ebenfalls beendet. Die Ursache wurde nicht eingegrenzt; dies ist keine nachgewiesene Produktursache.
- Kein Live-Test gegen eine Universität, keine realen Scheduler installiert, keine macOS-/Linux-Desktopvergleichsmessung und kein abgeschlossener Security-Skill-Scan.

## Ursprünglich empfohlene Reihenfolge

1. Konfigurationsfehler 01–05 und die Pfadbindung von Background-Jobs 26 behandeln.
2. Sortier-/Terminprobleme 07–12 und unvollständige Regelanzeige/-simulation 18–19 beheben.
3. Schmale Dialoge, Pfaddetails und Dokumentation 06, 13–17, 22–25 verbessern.
4. Schreibkonkurrenz und Wiederherstellung 27–30 gezielt absichern; Sicherheitsprüfung 38–45 nach verfügbarem Scan-Setup durchführen.
5. Performance, Barrierefreiheit, zusätzliche Kategorien und Modulaufteilung anhand gemessener Probleme angehen.

PR integration: current main (`51452a5`) was merged before review. Its scoped
PDF-password storage, OS-keyring restrictions, safe-by-default publication, ZIP
limits/retries and undo checkpoints are preserved in the split workflow modules.
The audit regression tests use the existing merge-result API for refused writes.
