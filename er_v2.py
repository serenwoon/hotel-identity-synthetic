"""v2 — CI 단계를 v1 판정 앞에 붙이고, CI를 아는 기준선(B4·B5)과 판정 범위 지표를 더한다.

v1 판정 규칙은 er.py에서 그대로 가져온다(고치지 않는다).
"""
import time
from collections import defaultdict

import er


class CIUF(er.UF):
    """군집마다 CI 하나를 기억하는 union-find. 서로 다른 CI 군집은 잇지 않는다."""

    def __init__(self, records):
        super().__init__(len(records))
        self.ci = [r["ci"] or None for r in records]

    def can_union(self, a, b):
        ca, cb = self.ci[self.find(a)], self.ci[self.find(b)]
        return ca is None or cb is None or ca == cb

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            c = self.ci[ra] or self.ci[rb]
            self.p[rb] = ra
            self.ci[ra] = c


def ci_groups(records):
    g = defaultdict(list)
    for r in records:
        if r["ci"]:
            g[r["ci"]].append(r["rid"])
    return g


def run_method_v2(records, reg):
    t0 = time.perf_counter()
    er.prepare(records)
    pairs, capped = er.block(records, reg_v1_cap(reg))
    uf = CIUF(records)
    for ids in ci_groups(records).values():
        for x in ids[1:]:
            uf.union(ids[0], x)
    sc = reg["v1"]["method"]["score"]
    merges, review = [], []
    for i, j in pairs:
        a, b = records[i], records[j]
        if a["ci"] and b["ci"]:
            continue  # 0단계가 정했다
        s, sim, dc = er.score_pair(a, b, sc)
        d = er.decide(s, sim, dc)
        if d == "merge":
            merges.append((-s, i, j))
        elif d == "review":
            review.append((i, j))
    ci_conflict = 0
    for _, i, j in sorted(merges):
        if uf.find(i) == uf.find(j):
            continue
        if uf.can_union(i, j):
            uf.union(i, j)
        else:
            review.append((i, j))
            ci_conflict += 1
    return uf, review, pairs, capped, ci_conflict, time.perf_counter() - t0


def reg_v1_cap(reg):
    return reg["v1"]["method"]["blocking"]["block_cap"]


def run_b4(records, safe):
    uf = CIUF(records) if safe else er.UF(len(records))
    groups = [ids for ids in ci_groups(records).values()]
    contact = defaultdict(list)
    for r in records:
        if r["_ph"]:
            contact[("ph", r["_ph"])].append(r["rid"])
        if r["_em"]:
            contact[("em", r["_em"])].append(r["rid"])
    for ids in groups + list(contact.values()):
        for x in ids[1:]:
            if safe and not uf.can_union(ids[0], x):
                continue
            uf.union(ids[0], x)
    return uf


def judgment_counts(records, uf):
    """CI 없는 기록이 하나라도 낀 같은 사람 쌍: 전체 수와 같은 군집에 든 수."""
    per_pid = defaultdict(lambda: [0, 0])
    per_cl = defaultdict(lambda: [0, 0])
    for r in records:
        has = 1 if r["ci"] else 0
        per_pid[r["pid"]][0] += 1
        per_pid[r["pid"]][1] += has
        k = (uf.find(r["rid"]), r["pid"])
        per_cl[k][0] += 1
        per_cl[k][1] += has
    total = sum(er.c2(n) - er.c2(c) for n, c in per_pid.values())
    got = sum(er.c2(n) - er.c2(c) for n, c in per_cl.values())
    return total, got


def evaluate_v2(persons, links, records, reg):
    link_type = {(min(a, b), max(a, b)): t for a, b, t in links}
    per_pid = defaultdict(int)
    for r in records:
        per_pid[r["pid"]] += 1
    true_pairs = sum(er.c2(n) for n in per_pid.values())

    uf, review, pairs, capped, ci_conflict, secs = run_method_v2(records, reg)
    same, false, by_trap, false_records = er.cluster_metrics(records, uf, link_type)
    jt, jg = judgment_counts(records, uf)
    rev_true = sum(1 for i, j in review if records[i]["pid"] == records[j]["pid"])
    up = er.UF(len(records))
    for x in range(len(records)):
        up.p[x] = uf.find(x)
    for i, j in review:
        if records[i]["pid"] == records[j]["pid"]:
            up.union(i, j)
    same_up, _, _, _ = er.cluster_metrics(records, up, link_type)
    n = len(records)
    out = {
        "records": n, "persons": len(persons), "true_pairs": true_pairs, "judgment_true_pairs": jt,
        "ci_record_share": sum(1 for r in records if r["ci"]) / n,
        "method": {
            "auto_recall": same / true_pairs, "judgment_recall": jg / jt if jt else None,
            "false_merge_pairs": false, "false_merge_by_trap": by_trap, "false_merged_records": false_records,
            "review_pairs": len(review), "review_per_1000": 1000 * len(review) / n,
            "review_same_person_share": rev_true / len(review) if review else None,
            "ci_conflict_to_review": ci_conflict,
            "recall_upper_if_human_perfect": same_up / true_pairs,
            "comparisons": len(pairs), "capped_blocks": capped, "seconds": round(secs, 2),
        },
    }
    bases = {"B1": er.run_baseline(records, "B1"), "B3": er.run_baseline(records, "B3"),
             "B4": run_b4(records, safe=False), "B5": run_b4(records, safe=True)}
    for k, buf in bases.items():
        s2, f2, bt2, fr2 = er.cluster_metrics(records, buf, link_type)
        _, jg2 = judgment_counts(records, buf)
        out[k] = {"auto_recall": s2 / true_pairs, "judgment_recall": jg2 / jt if jt else None,
                  "false_merge_pairs": f2, "false_merge_by_trap": bt2, "false_merged_records": fr2}
    m = out["method"]
    out["beats"] = {k: (m["auto_recall"] > out[k]["auto_recall"] and m["false_merge_pairs"] <= out[k]["false_merge_pairs"])
                    for k in bases}
    out["false_le_B5"] = m["false_merge_pairs"] <= out["B5"]["false_merge_pairs"]
    return out
