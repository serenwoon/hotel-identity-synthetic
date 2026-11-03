"""호텔 동일인 판정 합성 사전 실측 — 생성기 · 판정기 · 기준선 · 지표.

표준 라이브러리만 쓴다. 규칙과 문턱은 registry.json과 같아야 한다.
"""
import random
import re
import time
from collections import defaultdict
from itertools import combinations

# ---------------------------------------------------------------- 이름 재료

SURNAMES = [  # (한글, 영문 표기 변형, 가중치)
    ("김", ["Kim", "Gim"], 21.5), ("이", ["Lee", "Yi", "Rhee"], 14.7),
    ("박", ["Park", "Pak", "Bak"], 8.4), ("최", ["Choi", "Choe"], 4.7),
    ("정", ["Jung", "Jeong", "Chung"], 4.3), ("강", ["Kang", "Gang"], 2.3),
    ("조", ["Cho", "Jo"], 2.1), ("윤", ["Yoon", "Yun"], 2.1),
    ("장", ["Jang", "Chang"], 2.0), ("임", ["Lim", "Im", "Rim"], 1.6),
    ("한", ["Han"], 1.5), ("오", ["Oh", "O"], 1.5), ("서", ["Seo", "Suh"], 1.5),
    ("신", ["Shin", "Sin"], 1.5), ("권", ["Kwon", "Gwon"], 1.4),
    ("황", ["Hwang"], 1.4), ("안", ["Ahn", "An"], 1.4), ("송", ["Song"], 1.3),
    ("류", ["Ryu", "Yoo", "Yu"], 1.2), ("홍", ["Hong"], 1.1),
]

GIVEN = {
    "민": ["Min"], "서": ["Seo", "Suh"], "준": ["Jun", "Joon"], "지": ["Ji", "Jee"],
    "현": ["Hyun", "Hyeon"], "우": ["Woo", "U"], "은": ["Eun"], "수": ["Su", "Soo"],
    "영": ["Young", "Yeong", "Yong"], "하": ["Ha"], "윤": ["Yoon", "Yun"],
    "도": ["Do", "Doh"], "예": ["Ye", "Yeh"], "진": ["Jin"], "성": ["Sung", "Seong"],
    "호": ["Ho"], "연": ["Yeon", "Yun"], "주": ["Ju", "Joo"], "승": ["Seung"],
    "재": ["Jae", "Jay"], "태": ["Tae"], "경": ["Kyung", "Gyeong", "Kyeong"],
    "원": ["Won", "Weon"], "혜": ["Hye", "Hae"], "나": ["Na"], "유": ["Yu", "Yoo"],
    "아": ["A", "Ah"], "동": ["Dong"], "상": ["Sang"], "희": ["Hee", "Hui"],
    "미": ["Mi"], "석": ["Seok", "Suk"], "철": ["Cheol", "Chul"],
    "정": ["Jung", "Jeong"], "훈": ["Hoon", "Hun"], "혁": ["Hyuk", "Hyeok"],
    "빈": ["Bin"], "채": ["Chae"], "다": ["Da"],
}
GIVEN_KEYS = list(GIVEN)
DOMAINS = ["gmail.com", "naver.com", "daum.net", "kakao.com", "hanmail.net"]
SOURCES = ["hotel_rewards", "resort_member", "legacy_property"]


def pick_variant(rng, variants):
    if len(variants) == 1 or rng.random() < 0.65:
        return variants[0]
    return rng.choice(variants[1:])


# ---------------------------------------------------------------- 생성기

def make_phone(rng):
    return "010" + "".join(str(rng.randint(0, 9)) for _ in range(8))


USED_EMAILS = set()  # 생성 한 번 안에서 이메일 주소는 한 사람만 쓴다(함정의 공유는 share()가 따로 복사)


def make_email(rng, given_ko, sur_ko):
    sur = dict((s, v) for s, v, _ in SURNAMES)[sur_ko][0].lower()
    giv = "".join(GIVEN[g][0].lower() for g in given_ko)
    handle = rng.choice([f"{giv}{sur}", f"{giv}.{sur}", f"{sur}{giv}", f"{giv[:1]}{sur}"])
    if rng.random() < 0.7:
        handle += str(rng.randint(1, 9999))
    domain = rng.choice(DOMAINS)
    email = f"{handle}@{domain}"
    while email in USED_EMAILS:
        email = f"{handle}{rng.randint(1, 99999)}@{domain}"
    USED_EMAILS.add(email)
    return email


def make_dob(rng, year_lo=1950, year_hi=2005):
    return (rng.randint(year_lo, year_hi), rng.randint(1, 12), rng.randint(1, 28))


def shift_dob(rng, dob, lo, hi):
    y = dob[0] + rng.choice([-1, 1]) * rng.randint(lo, hi)
    return (max(1920, min(2008, y)), rng.randint(1, 12), rng.randint(1, 28))


def new_person(rng, pid, surname=None, given=None, dob=None):
    sur = surname or rng.choices([s for s, _, _ in SURNAMES], [w for _, _, w in SURNAMES])[0]
    giv = given or "".join(rng.choice(GIVEN_KEYS) for _ in range(2))
    return {"pid": pid, "sur": sur, "giv": giv, "dob": dob or make_dob(rng),
            "phone": make_phone(rng), "email": make_email(rng, giv, sur)}


def generate(seed, base_persons, reg):
    rng = random.Random(seed)
    USED_EMAILS.clear()
    persons, links = [], []
    for i in range(base_persons):
        persons.append(new_person(rng, i))
    traps = reg["data"]["traps_must_not_merge"]
    nxt = len(persons)
    for base in list(persons):
        f = traps["family_same_surname"]
        if rng.random() < f["rate"]:
            p = new_person(rng, nxt, surname=base["sur"],
                           dob=shift_dob(rng, base["dob"], *f["dob_gap_years"]))
            while p["giv"] == base["giv"]:
                p["giv"] = "".join(rng.choice(GIVEN_KEYS) for _ in range(2))
            share(rng, base, p, f)
            persons.append(p); links.append((base["pid"], nxt, "가족(같은 성)")); nxt += 1
        s = traps["spouse"]
        if rng.random() < s["rate"]:
            p = new_person(rng, nxt, dob=shift_dob(rng, base["dob"], *s["dob_gap_years"]))
            share(rng, base, p, s)
            persons.append(p); links.append((base["pid"], nxt, "배우자")); nxt += 1
        t = traps["twin"]
        if rng.random() < t["rate"]:
            first = base["giv"][0] if rng.random() < t["same_first_given_syllable"] else rng.choice(GIVEN_KEYS)
            giv = first + rng.choice(GIVEN_KEYS)
            while giv == base["giv"]:
                giv = first + rng.choice(GIVEN_KEYS)
            p = new_person(rng, nxt, surname=base["sur"], given=giv, dob=base["dob"])
            share(rng, base, p, t)
            persons.append(p); links.append((base["pid"], nxt, "쌍둥이")); nxt += 1
        d = traps["same_name_same_dob"]
        if rng.random() < d["rate"]:
            p = new_person(rng, nxt, surname=base["sur"], given=base["giv"], dob=base["dob"])
            persons.append(p); links.append((base["pid"], nxt, "동명이인·생일 같음")); nxt += 1

    records = []
    rpp = reg["data"]["records_per_person"]
    counts, weights = [int(k) for k in rpp], list(rpp.values())
    for p in persons:
        n = rng.choices(counts, weights)[0]
        srcs = rng.sample(SOURCES, n)
        for k in range(n):
            records.append(make_record(rng, p, k, srcs[k], reg, len(records)))
    return persons, links, records


def share(rng, base, p, spec):
    if rng.random() < spec["share_email"]:
        p["email"] = base["email"]
    if rng.random() < spec["share_phone"]:
        p["phone"] = base["phone"]


def english_name(rng, p, fmt):
    sur_vars = dict((s, v) for s, v, _ in SURNAMES)[p["sur"]]
    sur = pick_variant(rng, sur_vars)
    sylls = [pick_variant(rng, GIVEN[g]) for g in p["giv"]]
    r = rng.random()
    if r < fmt["given_joined"]:
        giv = sylls[0] + "".join(s.lower() for s in sylls[1:])
    elif r < fmt["given_joined"] + fmt["given_hyphen"]:
        giv = sylls[0] + "-" + "-".join(s.lower() for s in sylls[1:])
    else:
        giv = " ".join(sylls)
    name = f"{sur} {giv}" if rng.random() < fmt["order_surname_first"] else f"{giv} {sur}"
    return name.upper() if rng.random() < fmt["upper_case"] else name


def typo_en(rng, s):
    idx = [i for i, c in enumerate(s) if c.isalpha()]
    i = rng.choice(idx)
    c = rng.choice("abcdefghijklmnopqrstuvwxyz")
    c = c.upper() if s[i].isupper() else c
    return s[:i] + c + s[i + 1:]


def fmt_phone(rng, ph, formats):
    a, b, c = ph[:3], ph[3:7], ph[7:]
    f = rng.randrange(len(formats))
    return [f"{a}-{b}-{c}", f"{a}{b}{c}", f"+82 {a[1:]}-{b}-{c}", f"{a} {b} {c}"][f]


def case_email(rng, e):
    return "".join(ch.upper() if rng.random() < 0.3 else ch for ch in e)


def make_record(rng, p, k, src, reg, rid):
    d = reg["data"]
    rec = {"rid": rid, "pid": p["pid"], "src": src, "name_ko": "", "name_en": ""}
    if rng.random() < d["name_form"]["korean"]:
        name = p["sur"] + p["giv"]
        if rng.random() < d["name_typo"]:
            j = rng.randrange(1, len(name))
            name = name[:j] + rng.choice(GIVEN_KEYS) + name[j + 1:]
        rec["name_ko"] = name
    else:
        name = english_name(rng, p, d["english_format"])
        if rng.random() < d["name_typo"]:
            name = typo_en(rng, name)
        rec["name_en"] = name
    ph = p["phone"]
    if k >= 1 and rng.random() < d["phone"]["changed_from_second_record"]:
        ph = make_phone(rng)
    rec["phone"] = "" if rng.random() < d["phone"]["missing"] else fmt_phone(rng, ph, d["phone"]["formats"])
    em = p["email"]
    if k >= 1 and rng.random() < d["email"]["changed_from_second_record"]:
        em = make_email(rng, p["giv"], p["sur"])
    if rng.random() < d["email"]["missing"]:
        em = ""
    elif rng.random() < d["email"]["case_variant"]:
        em = case_email(rng, em)
    rec["email"] = em
    y, m, dd = p["dob"]
    if rng.random() < d["dob"]["missing"]:
        rec["dob"] = ""
    else:
        if rng.random() < d["dob"]["typo"]:
            dd = dd + 1 if dd < 28 else dd - 1
        rec["dob"] = f"{y:04d}-{m:02d}-{dd:02d}"
    return rec


# ---------------------------------------------------------------- 정규화

CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi",
        "yu", "eu", "ui", "i"]
JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t",
        "ng", "t", "t", "k", "t", "p", "t"]
assert len(CHO) == 19 and len(JUNG) == 21 and len(JONG) == 28


def rr(s):
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out.append(CHO[c // 588] + JUNG[(c % 588) // 28] + JONG[c % 28])
        else:
            out.append(ch)
    return "".join(out)


def tok_key(t):
    t = re.sub(r"[^a-z]", "", t.lower())
    for a, b in (("rh", "r"), ("sh", "s"), ("ch", "j")):
        t = t.replace(a, b)
    for a, b in (("ee", "i"), ("oo", "u"), ("ou", "u"), ("eo", "u"), ("oe", "oi")):
        t = t.replace(a, b)
    t = re.sub(r"(?<=[aeiouwy])h", "", t)
    t = t.translate(str.maketrans("gbdr", "kptl"))
    if len(t) > 1 and t[0] == "l" and t[1] in "iy":
        t = t[1:]
    return re.sub(r"(.)\1+", r"\1", t)


def name_keys(rec):
    if rec["name_ko"]:
        n = rec["name_ko"]
        return [tok_key(rr(n[0])) + tok_key(rr(n[1:]))]
    toks = [tok_key(x) for x in rec["name_en"].replace("-", "").split() if x]
    toks = [x for x in toks if x]
    if not toks:
        return [""]
    cands = {"".join(toks), "".join(toks[-1:] + toks[:-1]), "".join(toks[1:] + toks[:1])}
    return sorted(cands)


def norm_phone(p):
    d = re.sub(r"\D", "", p)
    if d.startswith("82"):
        d = "0" + d[2:]
    return d


def norm_email(e):
    return e.strip().lower()


def jaro_winkler(a, b):
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    win = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    m = 0
    for i in range(la):
        for j in range(max(0, i - win), min(lb, i + win + 1)):
            if not mb[j] and a[i] == b[j]:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, j = 0, 0
    for i in range(la):
        if ma[i]:
            while not mb[j]:
                j += 1
            if a[i] != b[j]:
                t += 1
            j += 1
    jaro = (m / la + m / lb + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return jaro + p * 0.1 * (1 - jaro)


# ---------------------------------------------------------------- 판정

def prepare(records):
    for r in records:
        r["_ph"] = norm_phone(r["phone"]) if r["phone"] else ""
        r["_em"] = norm_email(r["email"]) if r["email"] else ""
        r["_nk"] = name_keys(r)


def block(records, cap):
    blocks = defaultdict(list)
    for r in records:
        if r["dob"]:
            blocks[("dob", r["dob"])].append(r["rid"])
        if r["_em"]:
            blocks[("em", r["_em"])].append(r["rid"])
        if r["_ph"]:
            blocks[("ph", r["_ph"])].append(r["rid"])
        for k in r["_nk"]:
            if k:
                blocks[("nm", k)].append(r["rid"])
    pairs, capped = set(), 0
    for ids in blocks.values():
        ids = sorted(set(ids))
        if len(ids) > cap:
            capped += 1
            continue
        pairs.update(combinations(ids, 2))
    return pairs, capped


def score_pair(a, b, sc):
    s = 0
    dob_conflict = False
    if a["dob"] and b["dob"]:
        if a["dob"] == b["dob"]:
            s += sc["dob"]["both_equal"]
        else:
            s += sc["dob"]["both_present_differ"]
            dob_conflict = True
    if a["_em"] and a["_em"] == b["_em"]:
        s += sc["email"]["both_equal"]
    if a["_ph"] and a["_ph"] == b["_ph"]:
        s += sc["phone"]["both_equal"]
    sim = max(jaro_winkler(x, y) for x in a["_nk"] for y in b["_nk"])
    if sim >= 0.95:
        s += sc["name"]["sim_ge_0.95"]
    elif sim >= 0.88:
        s += sc["name"]["sim_0.88_to_0.95"]
    else:
        s += sc["name"]["sim_lt_0.88"]
    return s, sim, dob_conflict


def decide(s, sim, dob_conflict):
    if s >= 7 and sim >= 0.95 and not dob_conflict:
        return "merge"
    if s >= 4:
        return "review"
    return "separate"


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def run_method(records, reg):
    t0 = time.perf_counter()
    prepare(records)
    pairs, capped = block(records, reg["method"]["blocking"]["block_cap"])
    sc = reg["method"]["score"]
    uf = UF(len(records))
    review = []
    for i, j in pairs:
        s, sim, dc = score_pair(records[i], records[j], sc)
        d = decide(s, sim, dc)
        if d == "merge":
            uf.union(i, j)
        elif d == "review":
            review.append((i, j))
    secs = time.perf_counter() - t0
    return uf, review, pairs, capped, secs


def run_baseline(records, kind):
    uf = UF(len(records))
    groups = defaultdict(list)
    for r in records:
        if kind == "B1":
            if r["phone"]:
                groups[("ph", r["phone"])].append(r["rid"])
            if r["email"]:
                groups[("em", r["email"])].append(r["rid"])
        elif kind == "B2":
            if r["phone"] and r["email"]:
                groups[(r["phone"], r["email"])].append(r["rid"])
        elif kind == "B3":
            if r["_ph"]:
                groups[("ph", r["_ph"])].append(r["rid"])
            if r["_em"]:
                groups[("em", r["_em"])].append(r["rid"])
    for ids in groups.values():
        for x in ids[1:]:
            uf.union(ids[0], x)
    return uf


# ---------------------------------------------------------------- 지표

def c2(n):
    return n * (n - 1) // 2


def cluster_metrics(records, uf, link_type):
    clusters = defaultdict(lambda: defaultdict(int))
    for r in records:
        clusters[uf.find(r["rid"])][r["pid"]] += 1
    same, false, by_trap, false_records = 0, 0, defaultdict(int), 0
    for members in clusters.values():
        same += sum(c2(n) for n in members.values())
        if len(members) > 1:
            false_records += sum(members.values())
            for (pa, na), (pb, nb) in combinations(sorted(members.items()), 2):
                false += na * nb
                by_trap[link_type.get((pa, pb), "무관한 사람")] += na * nb
    return same, false, dict(by_trap), false_records


def evaluate(persons, links, records, reg):
    link_type = {}
    for a, b, t in links:
        link_type[(min(a, b), max(a, b))] = t
    per_pid = defaultdict(list)
    for r in records:
        per_pid[r["pid"]].append(r["rid"])
    true_pairs = sum(c2(len(v)) for v in per_pid.values())
    trap_pairs = defaultdict(int)
    for a, b, t in links:
        trap_pairs[t] += len(per_pid[a]) * len(per_pid[b])

    uf, review, pairs, capped, secs = run_method(records, reg)
    same, false, by_trap, false_records = cluster_metrics(records, uf, link_type)
    rev_true = sum(1 for i, j in review if records[i]["pid"] == records[j]["pid"])
    uf_up = UF(len(records))
    for x in range(len(records)):
        uf_up.p[x] = uf.find(x)
    for i, j in review:
        if records[i]["pid"] == records[j]["pid"]:
            uf_up.union(i, j)
    same_up, _, _, _ = cluster_metrics(records, uf_up, link_type)
    blocked_true = sum(1 for i, j in pairs if records[i]["pid"] == records[j]["pid"])
    n = len(records)
    out = {
        "records": n, "persons": len(persons), "true_pairs": true_pairs,
        "trap_pairs": dict(trap_pairs),
        "method": {
            "auto_recall": same / true_pairs, "auto_same_pairs": same,
            "false_merge_pairs": false, "false_merge_by_trap": by_trap,
            "false_merged_records": false_records,
            "review_pairs": len(review), "review_same_person": rev_true,
            "recall_upper_if_human_perfect": same_up / true_pairs,
            "blocking_recall": blocked_true / true_pairs,
            "blocking_missed_true_pairs": true_pairs - blocked_true,
            "comparisons": len(pairs), "all_pairs": c2(n),
            "comparison_ratio": len(pairs) / c2(n), "capped_blocks": capped,
            "seconds": round(secs, 2),
        },
    }
    for kind in ("B1", "B2", "B3"):
        buf = run_baseline(records, kind)
        s2, f2, bt2, fr2 = cluster_metrics(records, buf, link_type)
        out[kind] = {"auto_recall": s2 / true_pairs, "auto_same_pairs": s2, "false_merge_pairs": f2,
                     "false_merge_by_trap": bt2, "false_merged_records": fr2}
    m = out["method"]
    out["beats"] = {k: (m["auto_recall"] > out[k]["auto_recall"]
                        and m["false_merge_pairs"] <= out[k]["false_merge_pairs"])
                    for k in ("B1", "B2", "B3")}
    return out
