"""사용: python -I -X utf8 run_v2.py smoke | main [출력폴더]

smoke — 인원 300, 시드 99. 돌아가는지만 본다(판정 숫자는 찍지 않는다).
main  — 등록된 base 다섯 시드 + 민감도 시나리오(시드 셋씩). 결과는 out_v2/ (기본).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "gen_v2"))
import er_v2  # noqa: E402
from gen_v2 import generate  # noqa: E402

reg = json.loads((HERE / "registry_v2.json").read_text(encoding="utf-8"))
reg["v1"] = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
out_dir = HERE / (sys.argv[2] if len(sys.argv) > 2 else "out_v2")
N = reg["generator"]["base_persons"]


def one(seed, scenario, persons_n):
    persons, links, records = generate(seed, persons_n, dict(scenario))
    return er_v2.evaluate_v2(persons, links, records, reg)


if mode == "smoke":
    res = one(99, {}, 300)
    print("smoke ok — records", res["records"], "persons", res["persons"],
          "ci share", round(res["ci_record_share"], 3))
    sys.exit(0)

out_dir.mkdir(exist_ok=True)
sc = reg["scenarios"]
base = {}
for seed in sc["base"]["seeds"]:
    res = one(seed, sc["base"]["scenario"], N)
    base[seed] = res
    m = res["method"]
    print(f"base seed {seed}: recall {m['auto_recall']:.4f} judg {m['judgment_recall']:.4f} false {m['false_merge_pairs']} "
          f"| B4 {res['B4']['auto_recall']:.4f}/{res['B4']['false_merge_pairs']} "
          f"| B5 {res['B5']['auto_recall']:.4f}/{res['B5']['false_merge_pairs']} "
          f"| review/1k {m['review_per_1000']:.1f}")
(out_dir / "base.json").write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

sens = {}
for name, scen in sc["sensitivity"].items():
    sens[name] = {}
    for seed in sc["sensitivity_seeds"]:
        res = one(seed, scen, N)
        sens[name][seed] = res
    rs = list(sens[name].values())
    avg = lambda f: sum(f(r) for r in rs) / len(rs)
    print(f"{name}: recall {avg(lambda r: r['method']['auto_recall']):.4f} "
          f"false {avg(lambda r: r['method']['false_merge_pairs']):.1f} "
          f"| B5 {avg(lambda r: r['B5']['auto_recall']):.4f}/{avg(lambda r: r['B5']['false_merge_pairs']):.1f} "
          f"| review/1k {avg(lambda r: r['method']['review_per_1000']):.1f}")
(out_dir / "sensitivity.json").write_text(json.dumps(sens, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

b4 = all(r["beats"]["B4"] for r in base.values())
b5 = all(r["beats"]["B5"] for r in base.values())
fle = all(r["false_le_B5"] for r in base.values())
verdict = "통과" if (b4 and b5) else ("부분 통과" if fle else "실패")
summary = {"verdict": verdict, "beats_B4_all": b4, "beats_B5_all": b5, "false_le_B5_all": fle,
           "per_seed": {s: r["beats"] for s, r in base.items()}}
(out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print("verdict:", verdict, summary["per_seed"])
