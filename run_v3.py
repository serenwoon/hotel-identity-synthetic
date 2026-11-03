"""v3 탐색 — 「이름 0.95 이상 + 전화나 이메일 하나 + 생일 충돌 없음」이면 합치는 조건을 더한다.

사용: python -I -X utf8 run_v3.py [출력폴더]   (기본 out_v3/)
v1 판정 코드(er.py)는 그대로 두고, 이 실행 안에서만 decide를 감싼다.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "gen_v2"))
import er  # noqa: E402
import er_v2  # noqa: E402
from gen_v2 import generate  # noqa: E402

reg3 = json.loads((HERE / "registry_v3.json").read_text(encoding="utf-8"))
reg = json.loads((HERE / "registry_v2.json").read_text(encoding="utf-8"))
reg["v1"] = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
out_dir = HERE / (sys.argv[1] if len(sys.argv) > 1 else "out_v3")
out_dir.mkdir(exist_ok=True)
N = reg["generator"]["base_persons"]

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


results = {}
for seed in reg3["seeds"]:
    persons, links, records = generate(seed, N, dict(reg3["scenario"]))
    er.score_pair, er.decide = v1_score_pair, v1_decide
    v2 = er_v2.evaluate_v2(persons, links, [dict(r) for r in records], reg)
    er.score_pair, er.decide = score_pair_v3, decide_v3
    v3 = er_v2.evaluate_v2(persons, links, [dict(r) for r in records], reg)
    er.score_pair, er.decide = v1_score_pair, v1_decide
    results[seed] = {"v2": v2, "v3": v3}
    m2, m3 = v2["method"], v3["method"]
    print(f"seed {seed}: v2 {m2['auto_recall']:.4f}/{m2['false_merge_pairs']} q{m2['review_per_1000']:.0f} | "
          f"v3 {m3['auto_recall']:.4f} judg {m3['judgment_recall']:.4f} false {m3['false_merge_pairs']} q{m3['review_per_1000']:.0f} | "
          f"B5 {v3['B5']['auto_recall']:.4f}/{v3['B5']['false_merge_pairs']}")
(out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
b4 = all(r["v3"]["beats"]["B4"] for r in results.values())
b5 = all(r["v3"]["beats"]["B5"] for r in results.values())
fle = all(r["v3"]["false_le_B5"] for r in results.values())
verdict = "통과" if (b4 and b5) else ("부분 통과" if fle else "실패")
(out_dir / "summary.json").write_text(json.dumps({"verdict": verdict, "status": "탐색", "beats_B4_all": b4, "beats_B5_all": b5,
                                                   "false_le_B5_all": fle}, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
print("v3 verdict (탐색):", verdict)
