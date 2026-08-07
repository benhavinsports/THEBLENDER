from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default=str(Path(__file__).resolve().parents[1]/'replay_output')); args=ap.parse_args()
    root=Path(args.root); grades=[]
    for p in sorted(root.glob('????-??-??/grade.json')):
        try: grades.append(json.loads(p.read_text()))
        except Exception: pass
    tickets=Counter(g.get('ticket') for g in grades)
    leak_counts=Counter()
    for g in grades:
        for item in (((g.get('leakage') or {}).get('gate22') or {}).get('confirmed_leaks') or []):
            gate=item.get('removed_gate', item.get('gate'))
            leak_counts[str(gate if gate is not None else 'unknown')] += 1
    out={
        'graded_slates':len(grades), 'wins_3_of_3':tickets.get('WIN',0), 'losses':tickets.get('LOSS',0),
        'no_ticket':tickets.get('NO_TICKET',0), 'ticket_win_rate': (tickets.get('WIN',0)/(tickets.get('WIN',0)+tickets.get('LOSS',0)) if tickets.get('WIN',0)+tickets.get('LOSS',0) else None),
        'recipient_leaks_by_gate':dict(leak_counts),
    }
    print(json.dumps(out,indent=2,sort_keys=True))

if __name__=='__main__': main()
