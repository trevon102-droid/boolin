import pandas as pd

from pipeline.schedule_spots import game_spots


def _g(gameday, away, home, weekday="Sunday", gametime="13:00", spread=None, result=None, div=0,
       stadium="Home Stadium", stadium_id="XXX00", location="Home", overtime=0):
    return {"gameday": gameday, "away_team": away, "home_team": home, "weekday": weekday, "gametime": gametime,
            "spread_line": spread, "result": result, "div_game": div, "stadium": stadium,
            "stadium_id": stadium_id, "location": location, "overtime": overtime}


def test_dallas_thursday_after_houston_is_a_trap():
    # 2026 Weeks 4-5: DAL @ HOU on Sun Oct 4, then TB @ DAL on Thu Oct 8 with DAL -9.5.
    df = pd.DataFrame([
        _g("2026-09-27", "BAL", "DAL", spread=-3, result=-3),
        _g("2026-10-04", "DAL", "HOU", spread=3, result=-4),
        _g("2026-10-08", "TB", "DAL", weekday="Thursday", gametime="20:15", spread=9.5),
        _g("2026-10-18", "DAL", "PHI", gametime="16:25", div=1),
    ])
    s = game_spots(df, df.iloc[2])
    dal = s["home"]
    assert dal["rest"] == 4
    assert "SHORT_WEEK_AFTER_ROAD" in dal["flags"] and "TRAP" in dal["flags"]
    assert dal["cap_tier"] == "C" and not dal["hard_pass"]
    assert not s["game_hard_pass"]


def test_international_return_without_bye_is_hard_pass():
    df = pd.DataFrame([
        _g("2026-09-04", "KC", "LAC", weekday="Friday", gametime="20:00", stadium="Neo Química Arena",
           stadium_id="SAO00", location="Neutral", spread=-3, result=3),
        _g("2026-09-14", "LAC", "LV", weekday="Monday", gametime="20:15", spread=-1),
    ])
    s = game_spots(df, df.iloc[1])
    assert s["away"]["flags"][0] == "INTL_RETURN" and s["away"]["hard_pass"]
    assert s["game_hard_pass"] and s["hard_pass_teams"] == ["LAC"]
    assert not s["home"]["hard_pass"]


def test_bye_after_international_clears_the_flag():
    df = pd.DataFrame([
        _g("2026-10-11", "PHI", "JAX", gametime="09:30", stadium="Tottenham Hotspur Stadium",
           stadium_id="LON02", location="Neutral"),
        _g("2026-10-25", "NYG", "PHI", spread=7),
    ])
    s = game_spots(df, df.iloc[1])
    assert "INTL_RETURN" not in s["home"]["flags"] and "OFF_BYE" in s["home"]["flags"]
