# -*- coding: utf-8 -*-
"""gen_v2 자체 점검. 실행: python -I -X utf8 selfcheck.py

generate(1, 20000, {}) 요약 통계 + 시나리오 키별 반응(seed 2, 5000명) + 결정성 + 형식 검사.
매처를 쓰지 않는다. 정답(pid)과 persons만으로 데이터 성질을 잰다.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # -I는 스크립트 폴더를 sys.path에 넣지 않는다
import gen_v2  # noqa: E402
from gen_v2 import generate, norm_email, norm_phone  # noqa: E402

KEYS = {"rid", "pid", "kind", "name_ko", "name_en", "phone", "email", "dob", "ci"}
KINDS = {"online_member", "offline_member", "sns_member", "non_member_stay", "foreign_member"}
REQUIRED_LINK_TYPES = {"가족(같은 성)", "배우자", "쌍둥이", "동명이인·생일 같음"}
FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  [FAIL]", msg)


def pct(x, n):
    return f"{(100.0 * x / n if n else 0):.1f}%"


def link_index(links):
    s = set()
    for a, b, _ in links:
        s.add((a, b) if a < b else (b, a))
    return s


def linked(L, a, b):
    return ((a, b) if a < b else (b, a)) in L


def shared_report(records, persons, links, attr, with_indirect=False):
    """같은 문자열(정규화)을 2명 이상이 쓰는 경우와 그것이 links로 설명되는지.
    설명됨 = 그 값을 쓰는 '주인'(persons의 phones/email에 그 값이 있는 사람)끼리는 직접 링크,
             주인이 아닌 사용자는 주인 중 한 명과 직접 링크. 주인이 없으면 사용자 모든 쌍이 직접 링크.
    with_indirect: 직접 링크는 없고 같은 주인을 거쳐서만 이어지는 사용자 쌍 수도 센다."""
    norm = norm_phone if attr == "phone" else norm_email
    owners = defaultdict(set)
    for p in persons:
        vals = p["phones"] if attr == "phone" else [p["email"]] + p["emails_extra"]
        for v in vals:
            owners[norm(v)].add(p["pid"])
    users = defaultdict(set)
    for r in records:
        v = norm(r[attr])
        if v:
            users[v].add(r["pid"])
    L = link_index(links)
    shared = {v: u for v, u in users.items() if len(u) > 1}
    bad = []
    indirect = 0
    for v, u in shared.items():
        ow = owners.get(v, set())
        if ow:
            users_ow = ow & u
            ok = all(linked(L, x, y) for x in users_ow for y in users_ow if x < y)
            ok = ok and all(x in ow or any(linked(L, x, o) for o in ow) for x in u)
        else:
            us = sorted(u)
            ok = all(linked(L, x, y) for i, x in enumerate(us) for y in us[i + 1:])
        if not ok:
            bad.append((v, sorted(u)))
        us = sorted(u)
        indirect += sum(1 for i, x in enumerate(us) for y in us[i + 1:] if not linked(L, x, y))
    if with_indirect:
        return len(shared), bad, indirect
    return len(shared), bad


def basic_checks(persons, links, records, label):
    check([p["pid"] for p in persons] == list(range(len(persons))), f"{label}: pid가 0..n-1 연속이 아님")
    check([r["rid"] for r in records] == list(range(len(records))), f"{label}: rid가 0..n-1 연속이 아님")
    bad_keys = sum(1 for r in records if set(r) != KEYS)
    check(bad_keys == 0, f"{label}: 키가 다른 레코드 {bad_keys}")
    check(all(r["kind"] in KINDS for r in records), f"{label}: 알 수 없는 kind")
    check(all(r["dob"] == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["dob"]) for r in records), f"{label}: dob 형식")
    check(all(r["name_ko"] == "" or re.search("[가-힣]", r["name_ko"]) for r in records), f"{label}: name_ko에 한글 없음")
    check(all(r["name_ko"] or r["name_en"] for r in records), f"{label}: 이름이 둘 다 빈 레코드")
    ci_of = {}
    ok_ci = True
    for r in records:
        if r["ci"]:
            if ci_of.setdefault(r["pid"], r["ci"]) != r["ci"]:
                ok_ci = False
    check(ok_ci, f"{label}: 같은 사람의 CI가 다름")
    check(len(set(ci_of.values())) == len(ci_of), f"{label}: 다른 사람이 같은 CI")
    online = Counter(r["pid"] for r in records if r["kind"] == "online_member")
    check(max(online.values(), default=0) <= 1, f"{label}: 온라인 회원 계정이 2개인 사람")
    pf = {p["pid"]: p["foreign"] for p in persons}
    check(all(not r["ci"] and not r["name_ko"] for r in records if pf[r["pid"]]), f"{label}: 외국인 레코드에 CI·한글명")
    check(all(not pf[r["pid"]] for r in records if r["kind"] in ("online_member", "offline_member", "sns_member")),
          f"{label}: 외국인이 한국인 전용 kind")
    check(all(a != b for a, b, _ in links), f"{label}: 자기 자신 링크")
    check(all(r["pid"] < len(persons) for r in records), f"{label}: 없는 pid")


def ci_less_share(persons, records):
    pf = {p["pid"]: p["foreign"] for p in persons}
    kr = [r for r in records if not pf[r["pid"]]]
    return sum(1 for r in kr if not r["ci"]) / len(kr), len(records) / len(persons)


def pair_email_share(persons, links, records, types):
    em = defaultdict(set)
    for r in records:
        if r["email"]:
            em[r["pid"]].add(norm_email(r["email"]))
    pairs = [(a, b) for a, b, t in links if t in types]
    designed = sum(1 for a, b in pairs if persons[a]["email_shared_from"] == b or persons[b]["email_shared_from"] == a
                   or (persons[a]["email_shared_from"] is not None and persons[a]["email_shared_from"] == persons[b]["email_shared_from"]))
    realized = sum(1 for a, b in pairs if em[a] & em[b])
    return len(pairs), designed, realized


def digest(seed, n):
    out = generate(seed, n, {})
    return hashlib.sha256(json.dumps(out, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--digest":
        print(digest(int(sys.argv[2]), int(sys.argv[3])))
        return

    print("=" * 72)
    print("gen_v2 selfcheck — generate(1, 20000, {})")
    print("=" * 72)
    t0 = time.perf_counter()
    persons, links, records = generate(1, 20000, {})
    dt = time.perf_counter() - t0
    basic_checks(persons, links, records, "main")
    print(f"persons {len(persons):,} · records {len(records):,} · links {len(links):,} · runtime {dt:.2f}s")
    check(dt < 30, "20000명 생성이 30초 초과")

    per = Counter(r["pid"] for r in records)
    dist = Counter(per.values())
    mean = len(records) / len(persons)
    gt4 = sum(1 for v in per.values() if v > 4) / len(persons)
    print(f"\n[사람당 레코드] 평균 {mean:.2f} · 4개 초과 {gt4 * 100:.1f}% · 1개뿐 {pct(dist[1], len(persons))}")
    print("  분포: " + " ".join(f"{k}:{dist[k]}" for k in sorted(dist) if k <= 8) +
          f" 9+:{sum(v for k, v in dist.items() if k >= 9)} (최대 {max(per.values())})")
    check(min(per[p['pid']] for p in persons) >= 1, "레코드가 없는 사람")

    kinds = Counter(r["kind"] for r in records)
    print("\n[kind별 레코드]")
    for k in ("online_member", "offline_member", "sns_member", "non_member_stay", "foreign_member"):
        print(f"  {k:<16} {kinds[k]:>6}  {pct(kinds[k], len(records))}")

    ci_n = sum(1 for r in records if r["ci"])
    cil, _ = ci_less_share(persons, records)
    print(f"\n[CI] CI 있는 레코드 {pct(ci_n, len(records))} · 한국인 레코드 중 CI 없음 {cil * 100:.1f}% (목표 base≈45%)")

    only_en = sum(1 for r in records if r["name_en"] and not r["name_ko"])
    only_ko = sum(1 for r in records if r["name_ko"] and not r["name_en"])
    both = sum(1 for r in records if r["name_ko"] and r["name_en"])
    pf = {p["pid"]: p["foreign"] for p in persons}
    kr_only_en = sum(1 for r in records if not pf[r["pid"]] and r["name_en"] and not r["name_ko"])
    print(f"\n[이름] 영문만 {pct(only_en, len(records))} (한국인 레코드 중 {kr_only_en}) · 한글만 {pct(only_ko, len(records))}"
          f" · 둘 다 {pct(both, len(records))}")

    print("\n[결측률 kind별: name_ko name_en phone email dob]")
    for k in sorted(kinds):
        rs = [r for r in records if r["kind"] == k]
        print(f"  {k:<16} " + " ".join(f"{a}:{pct(sum(1 for r in rs if not r[a]), len(rs)):>6}"
                                       for a in ("name_ko", "name_en", "phone", "email", "dob")))

    # 성 표기
    spell = {k: [s for s, _ in sp] for k, _, sp in gen_v2.SURNAMES}
    print("\n[영문 성 표기] 사람(본인 철자) / 레코드(영문명 있는 것)")
    for sur in ("이", "박", "정", "최"):
        ps = [p for p in persons if p["surname_ko"] == sur]
        pc = Counter(p["surname_en"] for p in ps)
        rc = Counter()
        variants = {v.lower() for v in spell[sur]} | {"박": {"bak"}, "정": {"jeong"}, "최": {"choe"}}.get(sur, set())
        for r in records:
            if r["name_en"] and persons[r["pid"]]["surname_ko"] == sur:
                toks = re.split(r"[\s,/]+", r["name_en"].lower())
                ends = [toks[0], toks[-1]] if toks else []
                hit = next((t for t in ends if t in variants), None)
                rc[hit.capitalize() if hit else "(못 찾음/오타)"] += 1
        n_p, n_r = len(ps), sum(rc.values())
        print(f"  {sur} 사람 {n_p}: " + ", ".join(f"{k} {pct(v, n_p)}" for k, v in pc.most_common(5)))
        print(f"     레코드 {n_r}: " + ", ".join(f"{k} {pct(v, n_r)}" for k, v in rc.most_common(5)))

    # 공유
    print("\n[공유 문자열]")
    for attr in ("email", "phone"):
        n_sh, bad, ind = shared_report(records, persons, links, attr, True)
        print(f"  {attr}: 2명 이상이 쓰는 값 {n_sh}개 · links로 설명 안 되는 것 {len(bad)}개"
              f" · 직접 링크 없이 같은 주인을 거쳐 이어지는 사용자 쌍 {ind}")
        for v, u in bad[:5]:
            print("     예:", v, u)
        check(not bad, f"{attr} 공유 중 links로 설명 안 되는 것 {len(bad)}")

    lt = Counter(t for _, _, t in links)
    print("\n[링크 종류] " + " · ".join(f"{t} {n}" for t, n in lt.most_common()))
    check(REQUIRED_LINK_TYPES <= set(lt), "필수 링크 종류 누락: " + str(REQUIRED_LINK_TYPES - set(lt)))
    twins = lt["쌍둥이"]
    kr_n = sum(1 for p in persons if not p["foreign"])
    print(f"  쌍둥이 쌍 {twins} → 쌍둥이인 사람 {pct(2 * twins, kr_n)} (근거7 목표 1~2%)")
    check(0.01 <= 2 * twins / kr_n <= 0.02, "쌍둥이 비율이 1~2% 밖")
    sp_same = sum(1 for a, b, t in links if t == "배우자" and persons[a]["surname_ko"] and
                  persons[a]["surname_ko"] == persons[b]["surname_ko"])
    print(f"  한국인 배우자 중 같은 성 {sp_same}쌍(독립 추첨 결과)")
    np_, d_, r_ = pair_email_share(persons, links, records, {"배우자"})
    print(f"  배우자 {np_}쌍: 설계상 메일 공유 {pct(d_, np_)} · 레코드에서 실제 겹침 {pct(r_, np_)}")
    np_, d_, r_ = pair_email_share(persons, links, records, {"가족(같은 성)"})
    print(f"  같은 성 가족 {np_}쌍: 설계상 메일 공유 {pct(d_, np_)} · 레코드에서 실제 겹침 {pct(r_, np_)}")

    # 시나리오
    print("\n" + "=" * 72)
    print("시나리오 키별 반응 (seed 2, 5000명)")
    print("=" * 72)
    res = {}
    for lv, target in (("low", .25), ("base", .45), ("high", .65)):
        ps, ls, rs = generate(2, 5000, {"ci_less_level": lv})
        basic_checks(ps, ls, rs, f"ci_less={lv}")
        c, m = ci_less_share(ps, rs)
        res[lv] = c
        print(f"  ci_less_level={lv:<4} 한국인 레코드 CI 없음 {c * 100:5.1f}% (목표 {target * 100:.0f}%) · 사람당 레코드 {m:.2f}")
        check(abs(c - target) < 0.06, f"ci_less_level={lv}: {c:.3f}가 목표 {target}에서 6%p 넘게 벗어남")
    check(res["low"] < res["base"] < res["high"], "ci_less_level 순서가 low<base<high가 아님")

    prev = -1
    for lv in ("low", "base", "high"):
        ps, ls, rs = generate(2, 5000, {"email_share_level": lv})
        n_sh, bad = shared_report(rs, ps, ls, "email")
        n1, d1, r1 = pair_email_share(ps, ls, rs, {"배우자"})
        n2, d2, r2 = pair_email_share(ps, ls, rs, {"가족(같은 성)"})
        print(f"  email_share_level={lv:<4} 공유 메일 문자열 {n_sh:>4} · 배우자 설계 공유 {pct(d1, n1)} 실제 {pct(r1, n1)}"
              f" · 같은 성 가족 설계 {pct(d2, n2)} 실제 {pct(r2, n2)} · 미설명 {len(bad)}")
        check(not bad, f"email_share_level={lv}: 미설명 공유 {len(bad)}")
        check(n_sh > prev, f"email_share_level={lv}: 공유 수가 단계마다 늘지 않음")
        prev = n_sh

    prev = -1
    for pc_ in (0.0, 0.2, 0.5):
        ps, ls, rs = generate(2, 5000, {"phone_change": pc_})
        alt = {p["pid"]: norm_phone(p["phones"][1]) for p in ps if not p["foreign"] and len(p["phones"]) > 1}
        kr_ph = [r for r in rs if r["phone"] and r["pid"] in alt]
        on_alt = sum(1 for r in kr_ph if norm_phone(r["phone"]) == alt[r["pid"]])
        multi = Counter()
        for r in kr_ph:
            multi[r["pid"]] = multi[r["pid"]]
        per_p = defaultdict(set)
        for r in kr_ph:
            per_p[r["pid"]].add(norm_phone(r["phone"]))
        mp = sum(1 for v in per_p.values() if len(v) > 1)
        n_sh, bad = shared_report(rs, ps, ls, "phone")
        print(f"  phone_change={pc_:<4} 한국인 전화 레코드 중 옛 번호 {pct(on_alt, len(kr_ph))} · 번호가 2개 이상 보이는 사람 {mp}"
              f" · 공유 번호 {n_sh} · 미설명 {len(bad)}")
        check(on_alt > prev, f"phone_change={pc_}: 옛 번호 비율이 늘지 않음")
        check(not bad, f"phone_change={pc_}: 미설명 공유 {len(bad)}")
        prev = on_alt

    for lv in ("base", "stress"):
        ps, ls, rs = generate(2, 5000, {"typo_level": lv})
        on = [r for r in rs if r["kind"] == "online_member"]
        ko_bad = sum(1 for r in on if r["name_ko"] != ps[r["pid"]]["name_ko"])
        dob_bad = sum(1 for r in on if r["dob"] != ps[r["pid"]]["dob"])
        ph_bad = sum(1 for r in on if norm_phone(r["phone"]) not in {norm_phone(x) for x in ps[r["pid"]]["phones"]})
        print(f"  typo_level={lv:<6} 온라인 회원 레코드 오염: 한글명 {pct(ko_bad, len(on))} · 생일 {pct(dob_bad, len(on))}"
              f" · 전화 {pct(ph_bad, len(on))} (stress 목표≈10%·레코드당 최대 2속성)")
        if lv == "base":
            check(dob_bad == 0, "typo base인데 온라인 생일 오염")
        else:
            check(0.06 < dob_bad / len(on) < 0.12, "typo stress 생일 오염률이 10% 근처가 아님")

    for fs in (0.0, 0.05, 0.2):
        ps, ls, rs = generate(2, 5000, {"foreign_share": fs})
        basic_checks(ps, ls, rs, f"foreign_share={fs}")
        nf = sum(1 for p in ps if p["foreign"])
        fm = sum(1 for r in rs if r["kind"] == "foreign_member")
        print(f"  foreign_share={fs:<4} 외국인 {pct(nf, len(ps))} · foreign_member 레코드 {pct(fm, len(rs))}")
        check(abs(nf / len(ps) - fs) < 0.005, f"foreign_share={fs}: 외국인 비율 불일치")

    # 결정성: 해시 시드가 다른 두 프로세스에서 같은 결과
    print("\n[결정성]")
    cmd = [sys.executable, "-I", "-X", "utf8", os.path.abspath(__file__), "--digest", "7", "3000"]
    h1 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h2 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8").stdout.strip()
    h3 = digest(7, 3000)
    print(f"  seed 7 · 3000명: {h1[:16]} / {h2[:16]} / {h3[:16]}")
    check(h1 == h2 == h3 and len(h1) == 64, "같은 seed인데 결과가 다름")
    check(digest(8, 3000) != h3, "seed가 달라도 결과가 같음")

    print("\n" + ("PASS" if not FAILS else f"FAIL ({len(FAILS)}건)"))
    for f in FAILS:
        print("  -", f)
    sys.exit(0 if not FAILS else 1)


if __name__ == "__main__":
    main()
