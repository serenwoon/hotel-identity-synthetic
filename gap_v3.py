"""v3가 자동으로 못 합친 같은 사람 쌍을 원인별로 나눈다(시드 21~25, v3 탐색과 같은 조건).

사용: python -I -X utf8 gap_v3.py   → out_v3/gap.json
원인 분류는 생성기의 정답 필드(given_ko·old_given_ko·eng_nick)를 써서 사후에 한다. 판정에는 쓰지 않는다.
"""
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "gen_v2"))
import er  # noqa: E402
import er_v2  # noqa: E402
from gen_v2 import generate  # noqa: E402

reg = json.loads((HERE / "registry_v2.json").read_text(encoding="utf-8"))
reg["v1"] = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
reg3 = json.loads((HERE / "registry_v3.json").read_text(encoding="utf-8"))
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


def name_form(rec, p):
    """이 기록의 이름이 어떤 꼴인지(정답 필드로 판별)."""
    ko, en = rec["name_ko"], rec["name_en"]
    if p.get("old_given_ko") and ko and p["old_given_ko"] in ko:
        return "개명 전 이름"
    if p.get("eng_nick") and en and p["eng_nick"].lower() in en.lower():
        return "영어 이름"
    if ko and p.get("given_ko") and ko.replace(" ", "") == p["given_ko"]:
        return "성 없이 이름만"
    if not ko and not en:
        return "이름 없음"
    return ""


er.score_pair, er.decide = score_pair_v3, decide_v3
total = Counter()
by_outcome = Counter()
missed_total = 0
true_judg = 0
for seed in reg3["seeds"]:
    persons, links, records = generate(seed, reg["generator"]["base_persons"], {})
    pmap = {p["pid"]: p for p in persons}
    uf, review, pairs, _, _, _ = er_v2.run_method_v2(records, reg)
    rev = set(review)
    per = defaultdict(list)
    for r in records:
        per[r["pid"]].append(r["rid"])
    for pid, ids in per.items():
        p = pmap[pid]
        for i, j in combinations(sorted(ids), 2):
            a, b = records[i], records[j]
            if a["ci"] and b["ci"]:
                continue
            true_judg += 1
            if uf.find(i) == uf.find(j):
                continue
            missed_total += 1
            forms = {name_form(a, p), name_form(b, p)} - {""}
            if forms:
                cause = "이름 꼴: " + sorted(forms)[0]
            else:
                s, sim, dc = v1_score_pair(a, b, reg["v1"]["method"]["score"])
                if (i, j) not in pairs:
                    cause = "묶기에서 못 만남"
                elif dc:
                    cause = "생년월일 충돌(오타·음력 등)"
                elif sim < 0.95:
                    cause = "이름 표기 차이(로마자·오타·순서)"
                elif not ((a["_ph"] and a["_ph"] == b["_ph"]) or (a["_em"] and a["_em"] == b["_em"])):
                    cause = "이름은 맞는데 같은 연락처 없음"
                else:
                    cause = "기타"
            total[cause] += 1
            by_outcome["사람 확인 대기열" if (i, j) in rev or (j, i) in rev else "따로 둠·못 만남"] += 1
er.score_pair, er.decide = v1_score_pair, v1_decide

out = {"seeds": reg3["seeds"], "judgment_true_pairs": true_judg, "missed_pairs": missed_total,
       "by_cause": dict(total.most_common()), "by_outcome": dict(by_outcome)}
(HERE / "out_v3" / "gap.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print("CI 없는 기록이 낀 같은 사람 쌍", true_judg, "· 자동으로 못 합친 쌍", missed_total)
for k, v in total.most_common():
    print(f"  {k}: {v} ({v / missed_total:.1%})")
print(" ", dict(by_outcome))
