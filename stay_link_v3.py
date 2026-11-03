"""회원이 가진 비회원 투숙 기록(여행사·전화·워크인)이 그 회원 기록에 붙는지 v3 탐색 조건 그대로 잰다.

사용: python -I -X utf8 stay_link_v3.py   → out_v3/stay_link.json
「이을 단서 없음」은 전화가 비었거나 안심번호(0507)이고, 이메일이 비었거나 여행사·애플 중계 주소인 기록이다.
정답 필드(pid)는 집계에만 쓰고 판정에는 쓰지 않는다.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gen_v2"))
import er  # noqa: E402
import er_v2  # noqa: E402
from gen_v2 import generate  # noqa: E402

reg = json.loads((REPO / "registry_v2.json").read_text(encoding="utf-8"))
reg["v1"] = json.loads((REPO / "registry.json").read_text(encoding="utf-8"))
reg3 = json.loads((REPO / "registry_v3.json").read_text(encoding="utf-8"))
v1_score_pair, v1_decide = er.score_pair, er.decide
_ctx = {}


def score_pair_v3(a, b, sc):
    s, sim, dc = v1_score_pair(a, b, sc)
    _ctx["contact"] = bool((a["_ph"] and a["_ph"] == b["_ph"]) or (a["_em"] and a["_em"] == b["_em"]))
    return s, sim, dc


def decide_v3(s, sim, dc):
    d = v1_decide(s, sim, dc)
    if d != "merge" and sim >= 0.95 and not dc and _ctx.get("contact"):
        return "merge"
    return d


MEMBER = {"online_member", "offline_member", "sns_member", "foreign_member"}
RELAY = ("agoda-messaging.com", "expediapartnercentral.com", "booking.com", "privaterelay.appleid.com")


def no_clue(r):
    ph = r["phone"].replace("-", "").replace(" ", "")
    ph_dead = (not ph) or ph.startswith("0507")
    em = r["email"].lower()
    em_dead = (not em) or any(em.endswith(d) for d in RELAY)
    return ph_dead and em_dead


er.score_pair, er.decide = score_pair_v3, decide_v3
tot = Counter()
reg_tot = Counter()
for seed in reg3["seeds"]:
    persons, links, records = generate(seed, reg["generator"]["base_persons"], {})
    uf, review, pairs, _, _, _ = er_v2.run_method_v2(records, reg)
    rev = {tuple(sorted(x)) for x in review}
    by_pid = defaultdict(list)
    for r in records:
        by_pid[r["pid"]].append(r)
    clusters = defaultdict(set)
    for r in records:
        clusters[uf.find(r["rid"])].add(r["pid"])
    for pid, rs in by_pid.items():
        members = [r for r in rs if r["kind"] in MEMBER]
        stays = [r for r in rs if r["kind"] == "non_member_stay"]
        if not members or not stays:
            continue
        linked_here = 0
        for s in stays:
            tot["stay"] += 1
            root = uf.find(s["rid"])
            wrong = len(clusters[root]) > 1
            hit = any(uf.find(m["rid"]) == root for m in members)
            if wrong:
                tot["wrong_cluster"] += 1
            if hit:
                tot["auto"] += 1
                linked_here += 1
            elif any(tuple(sorted((s["rid"], m["rid"]))) in rev for m in members):
                tot["review"] += 1
            else:
                tot["missed"] += 1
                if no_clue(s):
                    tot["missed_no_clue"] += 1
            if no_clue(s):
                tot["no_clue"] += 1
        if len(stays) >= 2:
            reg_tot["regular"] += 1
            if linked_here == len(stays):
                reg_tot["regular_all_linked"] += 1
            if linked_here == 0:
                reg_tot["regular_none_linked"] += 1
    tot["persons"] += len(by_pid)
er.score_pair, er.decide = v1_score_pair, v1_decide

n = tot["stay"]
print("시드", reg3["seeds"])
print("회원 기록이 있는 사람의 비회원 투숙 기록", n)
for k in ("auto", "review", "missed", "missed_no_clue", "no_clue", "wrong_cluster"):
    print(f"  {k}: {tot[k]} ({tot[k] / n:.1%})")
print("비회원 투숙 2건 이상인 회원(숨은 단골)", reg_tot["regular"],
      f"· 모두 붙음 {reg_tot['regular_all_linked']} ({reg_tot['regular_all_linked'] / reg_tot['regular']:.1%})",
      f"· 하나도 안 붙음 {reg_tot['regular_none_linked']} ({reg_tot['regular_none_linked'] / reg_tot['regular']:.1%})")
print("사람 수 합", tot["persons"])
out = {"seeds": reg3["seeds"], "stays_of_members": n,
       "auto_linked": tot["auto"], "review": tot["review"], "missed": tot["missed"],
       "missed_no_clue": tot["missed_no_clue"], "no_clue": tot["no_clue"],
       "in_cluster_with_other_person": tot["wrong_cluster"],
       "members_with_2plus_stays": reg_tot["regular"],
       "all_stays_linked": reg_tot["regular_all_linked"], "no_stay_linked": reg_tot["regular_none_linked"]}
(REPO / "out_v3" / "stay_link.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8", newline="\n")
