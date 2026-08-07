def transfer_event(players: list[dict]) -> list[dict]:
    if len(players)<2: return players
    ordered=sorted(players,key=lambda p:p.get("ownership_score",0),reverse=True)
    top,second=ordered[0],ordered[1]
    top_score=top.get("ownership_score",0); second_score=second.get("ownership_score",0)
    gap_pct=((top_score-second_score)/abs(top_score)*100) if top_score else 100
    obvious=(top.get("hr_model_score",0)>=85 or top.get("hr",0)>=25)
    adjacent=abs((top.get("slot") or 9)-(second.get("slot") or 9))<=1
    if obvious and adjacent and gap_pct<=10:
        second["transfer_flag"]=True
        second["event_reason"]="HR event transferred from obvious profile to adjacent pressure-release hitter"
        return [second]
    return [top]


def remove_false_chalk(players: list[dict]) -> list[dict]:
    return [p for p in players if not (p.get("pull") is not None and p["pull"]<50) and not (p.get("hard_hit") is not None and p["hard_hit"]<40)]
