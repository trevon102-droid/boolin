"""Write a Sharp Board slate (the JSON written to the board) to an .xlsx workbook.

Usage: python -m pipeline.slate_xlsx slate.json out.xlsx

Sheets: Settings (inputs), Plays (straights, longshots, ladder rungs), Parlays, Top 10.
Edge, our line and unit size are live formulas off the FanDuel price and the Settings
sheet, so changing a price or a setting re-sizes the play.
"""
import json
import sys

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.formatting.rule import FormulaRule

FONT = "Arial"
BLUE = Font(name=FONT, color="0000FF")
BLACK = Font(name=FONT)
BOLD = Font(name=FONT, bold=True)
HEAD = Font(name=FONT, bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F3A70")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="D3DAE2")
WRAP = Alignment(wrap_text=True, vertical="top")

SETTINGS = [
    ("Bankroll ($)", 500, "$#,##0", "Money set aside for betting. Change to yours."),
    ("1 unit = % of bankroll", 0.01, "0.0%", "1% is standard."),
    ("Kelly fraction", 0.25, "0.00", "Quarter Kelly."),
    ("Model weight (0-1)", 0.5, "0.00", "How far to trust our number over the market prior. Research suggests 0.1-0.3 until the bet log proves the model."),
    ("Min edge, sides/totals", 0.015, "0.0%", "Below this, no stake."),
    ("Min edge, props", 0.03, "0.0%", "Props carry more vig and noise."),
    ("Min edge, longshots (relative)", 0.20, "0.0%", "TD, HR and goal bets."),
    ("Max units per play", 5, "0.00", ""),
    ("Max units per longshot", 3, "0.00", ""),
    ("Ladder Kelly haircut", 0.5, "0.00", "Rungs ride one outcome."),
    ("Tier A size (u)", 1, "0.00", "Research mode: a play's tier sets its stake. Price is info, not a gate."),
    ("Tier B size (u)", 0.5, "0.00", ""),
    ("Tier C size (u)", 0.25, "0.00", "Leans and longshots."),
]
# Settings!B2..B11 in the order above
S = {name: f"Settings!$B${i + 2}" for i, (name, *_rest) in enumerate(SETTINGS)}


def is_prop(x):
    text = f"{x.get('category', '')} {x.get('market', '')}"
    return any(k in text.lower() for k in ("prop", "td", "scorer"))


def header(ws, cols, widths):
    for c, (name, w) in enumerate(zip(cols, widths), start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font, cell.fill = HEAD, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "E2"
    ws.row_dimensions[1].height = 30


def build(slate, out):
    wb = Workbook()

    # ---------- Settings ----------
    st = wb.active
    st.title = "Settings"
    st["A1"], st["B1"], st["C1"] = "Setting", "Value", "Note"
    for c in ("A1", "B1", "C1"):
        st[c].font, st[c].fill = HEAD, HEAD_FILL
    for i, (name, val, fmt, note) in enumerate(SETTINGS, start=2):
        st.cell(row=i, column=1, value=name).font = BLACK
        v = st.cell(row=i, column=2, value=val)
        v.font, v.fill, v.number_format = BLUE, INPUT_FILL, fmt
        st.cell(row=i, column=3, value=note).font = BLACK
    r = len(SETTINGS) + 3
    st.cell(row=r, column=1, value="How to use").font = BOLD
    for j, line in enumerate([
        "Yellow cells with blue text are inputs. Edit them and every sheet re-sizes.",
        "Research mode: a play with a Tier (A/B/C, last column) is staked at that tier's size. 'Price check' is info only.",
        "On Plays, type the price you see in the FanDuel app into column 'FanDuel odds' to re-check a play before betting.",
        "Prices are as of the 'Price as of' column. Glance at the app before betting; a move of a few cents doesn't change a research play.",
        f"Slate: {slate.get('date', '')} - {slate.get('updated', '')}",
    ], start=1):
        st.cell(row=r + j, column=1, value=line).font = BLACK
    st.column_dimensions["A"].width = 32
    st.column_dimensions["B"].width = 12
    st.column_dimensions["C"].width = 90

    # ---------- Plays ----------
    ws = wb.create_sheet("Plays")
    cols = ["Type", "Sport", "Game", "Start", "Play", "Category", "FanDuel odds", "Price as of",
            "Market prior", "Model (raw)", "Model (blended)", "Implied by price", "Our line",
            "Price check", "Min edge (price mode)", "Units", "Stake ($)", "Manual pass", "Bet to", "Why + stats", "Tier"]
    widths = [11, 7, 22, 10, 38, 18, 11, 17, 10, 10, 11, 10, 9, 9, 9, 8, 9, 8, 26, 80, 6]
    header(ws, cols, widths)

    rows = []
    for p in slate.get("picks", []):
        if p.get("ladder"):
            continue
        rows.append(("Straight", p))
    for f in slate.get("featured", []):
        for pr in f.get("props", []):
            if pr.get("onBoard"):
                continue
            rows.append(("Top 10 prop", {**pr, "sport": f.get("sport", ""), "game": f.get("game", ""),
                                         "start": f.get("start", "")}))
    for x in slate.get("longshots", []):
        rows.append(("Longshot", x))
    for ld in slate.get("ladders", []):
        for rung in ld.get("rungs", []):
            rows.append(("Ladder rung", {**ld, **rung, "pick": f"{ld['pick']} {rung['label']}",
                                         "why": ld.get("why", ""), "prior": None}))

    r = 2
    for kind, x in rows:
        odds = x.get("odds")
        vals = [kind, x.get("sport", ""), x.get("game", ""), x.get("start", ""), x.get("pick", ""),
                x.get("category", x.get("market", "")), odds if odds not in ("", None) else None,
                (x.get("priceAt") or slate.get("pricesAt") or "").replace("T", " ")[:16],
                x.get("prior"), x.get("modelP")]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = BLUE if c in (7, 9, 10) else BLACK
        ws.cell(row=r, column=7).fill = INPUT_FILL
        G, I, J = f"G{r}", f"I{r}", f"J{r}"
        floor_ref = S["Min edge, longshots (relative)"] if kind == "Longshot" else (
            S["Min edge, props"] if is_prop(x) else S["Min edge, sides/totals"])
        cap_ref = S["Max units per longshot"] if kind == "Longshot" else S["Max units per play"]
        hair = f"*{S['Ladder Kelly haircut']}" if kind == "Ladder rung" else ""
        dec = f"IF({G}<0,1+100/-{G},1+{G}/100)"
        ws[f"K{r}"] = f'=IF({J}="","",IF({I}="",{J},{I}+{S["Model weight (0-1)"]}*({J}-{I})))'
        ws[f"L{r}"] = f'=IF({G}="","",IF({G}<0,-{G}/(-{G}+100),100/({G}+100)))'
        ws[f"M{r}"] = f'=IF(K{r}="","",IF(K{r}>=0.5,-ROUND(100*K{r}/(1-K{r}),0),ROUND(100*(1-K{r})/K{r},0)))'
        ws[f"N{r}"] = f'=IF(OR({G}="",K{r}=""),"",K{r}*{dec}-1)'
        ws[f"O{r}"] = f"={floor_ref}"
        kelly = f"(({dec}-1)*K{r}-(1-K{r}))/({dec}-1)"
        tier = (x.get("conviction") or "").strip().upper()
        tier = tier if tier in ("A", "B", "C") else None
        ws.cell(row=r, column=21, value=tier).font = BLUE
        tier_units = (f'MIN({cap_ref},IF(U{r}="A",{S["Tier A size (u)"]},IF(U{r}="B",{S["Tier B size (u)"]},'
                      f'{S["Tier C size (u)"]})){hair if kind == "Ladder rung" else ""})')
        if x.get("units") not in (None, ""):
            tier_units = f'MIN({cap_ref},{float(x["units"])})'
        ws[f"P{r}"] = (f'=IF(R{r}="Y",0,IF(U{r}<>"",{tier_units},IF(OR({G}="",K{r}=""),0,IF(N{r}<O{r},0,'
                       f'MIN({cap_ref},ROUND(MAX(0,{kelly})*{S["Kelly fraction"]}{hair}/{S["1 unit = % of bankroll"]}*4,0)/4)))))')
        ws[f"Q{r}"] = f"=P{r}*{S['Bankroll ($)']}*{S['1 unit = % of bankroll']}"
        ws.cell(row=r, column=18, value="Y" if x.get("pass") else None).font = BLUE
        ws.cell(row=r, column=19, value=x.get("betTo", ""))
        why = x.get("why", "")
        if x.get("inputs"):
            why += "\n" + "\n".join(f"- {i}" for i in x["inputs"])
        ws.cell(row=r, column=20, value=why)
        if x.get("factors"):
            txt = "\n".join(f"{f['adj']:+.1f}  {f['f']}" for f in x["factors"])
            ws[f"J{r}"].comment = Comment(f"Start: {x.get('priorLabel', 'market')}\n{txt}", "Sharp Board")
        for c in range(1, 22):
            cell = ws.cell(row=r, column=c)
            if c >= 19:
                cell.alignment = WRAP
            if c not in (7, 9, 10, 18, 21):
                cell.font = BLACK
        for col, fmt in (("I", "0.0%"), ("J", "0.0%"), ("K", "0.0%"), ("L", "0.0%"), ("M", "+0;-0"),
                         ("N", "0.0%;-0.0%;-"), ("O", "0.0%"), ("P", "0.00;-0.00;-"), ("Q", "$#,##0;($#,##0);-"),
                         ("G", "+0;-0")):
            ws[f"{col}{r}"].number_format = fmt
        r += 1
    last = r - 1
    if last >= 2:
        tab = Table(displayName="Plays", ref=f"A1:U{last}")
        tab.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
        ws.add_table(tab)
        green = PatternFill("solid", fgColor="DCF0E6")
        ws.conditional_formatting.add(f"A2:U{last}", FormulaRule(formula=[f"$P2>0"], fill=green))
        ws.conditional_formatting.add(f"N2:N{last}", FormulaRule(formula=["AND(ISNUMBER($N2),$N2<0)"], font=Font(name=FONT, color="AE3228")))
    tr = last + 2
    ws.cell(row=tr, column=15, value="Total units").font = BOLD
    ws.cell(row=tr, column=16, value=f"=SUM(P2:P{last})").font = BOLD
    ws.cell(row=tr, column=17, value=f"=SUM(Q2:Q{last})").font = BOLD
    ws.cell(row=tr, column=17).number_format = "$#,##0"
    ws.cell(row=tr, column=16).number_format = "0.00"

    # ---------- Parlays ----------
    pw = wb.create_sheet("Parlays")
    pcols = ["Parlay", "Leg", "Game", "Sport", "FanDuel odds", "Model %", "Decimal", "Staked?", "Why"]
    for c, (n, w) in enumerate(zip(pcols, [26, 40, 22, 7, 11, 9, 9, 8, 60]), start=1):
        cell = pw.cell(row=1, column=c, value=n)
        cell.font, cell.fill = HEAD, HEAD_FILL
        pw.column_dimensions[get_column_letter(c)].width = w
    r = 2
    for pl in slate.get("parlays", []):
        first = r
        for leg in pl.get("legs", []):
            pw.cell(row=r, column=1, value=pl.get("name", "")).font = BLACK
            pw.cell(row=r, column=2, value=leg.get("pick", "")).font = BLACK
            pw.cell(row=r, column=3, value=leg.get("game", "")).font = BLACK
            pw.cell(row=r, column=4, value=leg.get("sport", "")).font = BLACK
            o = pw.cell(row=r, column=5, value=leg.get("odds"))
            o.font, o.fill, o.number_format = BLUE, INPUT_FILL, "+0;-0"
            m = pw.cell(row=r, column=6, value=leg.get("modelP"))
            m.font, m.number_format = BLUE, "0.0%"
            pw[f"G{r}"] = f"=IF(E{r}<0,1+100/-E{r},1+E{r}/100)"
            pw[f"G{r}"].number_format = "0.000"
            r += 1
        lastleg = r - 1
        pw.cell(row=r, column=2, value="Parlay price (legs multiplied)").font = BOLD
        pw[f"G{r}"] = f"=PRODUCT(G{first}:G{lastleg})"
        pw[f"G{r}"].number_format = "0.00"
        pw[f"E{r}"] = f'=IF(G{r}>=2,ROUND((G{r}-1)*100,0),ROUND(-100/(G{r}-1),0))'
        pw[f"E{r}"].number_format = "+0;-0"
        pw[f"F{r}"] = f"=PRODUCT(F{first}:F{lastleg})"
        pw[f"F{r}"].number_format = "0.0%"
        pw.cell(row=r, column=8, value="No" if pl.get("pass") else "Yes").font = BOLD
        pw.cell(row=r, column=9, value=pl.get("why", "")).alignment = WRAP
        pw.cell(row=r + 1, column=2, value="Edge").font = BOLD
        pw[f"F{r + 1}"] = f"=F{r}*G{r}-1"
        pw[f"F{r + 1}"].number_format = "0.0%"
        r += 3

    # ---------- Hit rates ----------
    hw = wb.create_sheet("Hit rates")
    hcols = ["Sport", "Game", "Start", "Line", "Hits", "Of", "Rate", "FanDuel odds", "Log (newest first)", "Note"]
    for c, (n, w) in enumerate(zip(hcols, [7, 26, 10, 40, 6, 5, 7, 11, 34, 50]), start=1):
        cell = hw.cell(row=1, column=c, value=n)
        cell.font, cell.fill = HEAD, HEAD_FILL
        hw.column_dimensions[get_column_letter(c)].width = w
    hr = sorted(slate.get("hitrates", []), key=lambda h: -(h.get("hits", 0) / (h.get("of") or 1)))
    for i, h in enumerate(hr, start=2):
        vals = [h.get("sport"), h.get("game"), h.get("start"), h.get("pick"), h.get("hits"), h.get("of"), None,
                h.get("odds"), ", ".join(str(v) for v in h.get("log", [])), h.get("note", "")]
        for c, v in enumerate(vals, start=1):
            hw.cell(row=i, column=c, value=v).font = BLACK
        hw[f"G{i}"] = f"=IF(F{i}>0,E{i}/F{i},\"\")"
        hw[f"G{i}"].number_format = "0%"
        hw[f"H{i}"].number_format = "+0;-0"
    hw.freeze_panes = "E2"

    # ---------- Top 10 ----------
    tw = wb.create_sheet("Top 10")
    tcols = ["Rank", "Sport", "Game", "Start", "Headline", "Lines", "The read", "Injuries & news"]
    for c, (n, w) in enumerate(zip(tcols, [6, 7, 24, 10, 50, 50, 90, 60]), start=1):
        cell = tw.cell(row=1, column=c, value=n)
        cell.font, cell.fill = HEAD, HEAD_FILL
        tw.column_dimensions[get_column_letter(c)].width = w
    for i, f in enumerate(sorted(slate.get("featured", []), key=lambda z: z.get("rank", 99)), start=2):
        lines = "\n".join(f"{l['label']}: {l['value']} ({l.get('fair', '')})" for l in f.get("lines", []))
        vals = [f.get("rank"), f.get("sport"), f.get("game"), f.get("start"), f.get("headline"), lines,
                f.get("script"), "\n".join(f.get("injuries", []))]
        for c, v in enumerate(vals, start=1):
            cell = tw.cell(row=i, column=c, value=v)
            cell.font, cell.alignment = BLACK, WRAP
    tw.freeze_panes = "D2"

    # ---------- Notes ----------
    nw = wb.create_sheet("Notes")
    nw["A1"], nw["A1"].font = "Notes", BOLD
    nw["A2"] = slate.get("notes", "")
    nw["A2"].alignment = WRAP
    nw.column_dimensions["A"].width = 140
    nw["A4"], nw["A4"].font = "Rules for this slate", BOLD
    for i, rule in enumerate(slate.get("rules", []), start=5):
        nw.cell(row=i, column=1, value=f"- {rule}").alignment = WRAP

    wb.move_sheet("Plays", offset=-1)
    wb.active = 0
    wb.save(out)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src) as fh:
        build(json.load(fh), dst)
    print(f"wrote {dst}")
