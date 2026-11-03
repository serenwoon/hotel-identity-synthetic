"""사용: python -I -X utf8 run.py smoke | main

smoke — 시드 99, 인원 300. 돌아가는지만 본다(숫자는 보고에 쓰지 않음).
main  — 등록된 시드 다섯 개, 인원 20,000. out/ 에 결과를 쓴다.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import er  # noqa: E402

reg = json.loads((HERE / "registry.json").read_text(encoding="utf-8"))
mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
out_dir = HERE / (sys.argv[2] if len(sys.argv) > 2 else "out")
out_dir.mkdir(exist_ok=True)

if mode == "smoke":
    persons, links, records = er.generate(reg["data"]["seed_smoke"], 300, reg)
    res = er.evaluate(persons, links, records, reg)
    print("smoke ok — records", res["records"], "true_pairs", res["true_pairs"],
          "comparisons", res["method"]["comparisons"])
    sys.exit(0)

results = {}
for seed in reg["data"]["seeds_main"]:
    persons, links, records = er.generate(seed, reg["data"]["base_persons"], reg)
    res = er.evaluate(persons, links, records, reg)
    results[seed] = res
    (out_dir / f"seed{seed}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                              encoding="utf-8", newline="\n")
    m = res["method"]
    print(f"seed {seed}: recall {m['auto_recall']:.4f} false {m['false_merge_pairs']} | "
          f"B1 {res['B1']['auto_recall']:.4f}/{res['B1']['false_merge_pairs']} | "
          f"B3 {res['B3']['auto_recall']:.4f}/{res['B3']['false_merge_pairs']} | "
          f"review {m['review_pairs']} | {m['seconds']}s")

b1 = all(r["beats"]["B1"] for r in results.values())
b3 = all(r["beats"]["B3"] for r in results.values())
verdict = "통과" if (b1 and b3) else ("부분 통과" if b1 else "실패")
summary = {"verdict": verdict, "beats_B1_all_seeds": b1, "beats_B3_all_seeds": b3,
           "per_seed_beats": {s: r["beats"] for s, r in results.items()}}
(out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
print("verdict:", verdict, summary["per_seed_beats"])
