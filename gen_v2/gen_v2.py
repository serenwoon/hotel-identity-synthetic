# -*- coding: utf-8 -*-
"""gen_v2 — 한국 호텔 그룹 고객(로열티) 레코드 합성 생성기. 동일인 식별 실험용.

    generate(seed, base_persons, scenario) -> (persons, links, records)

표준 라이브러리만 쓴다. 같은 인자면 같은 결과가 나온다(random.Random(seed) 하나만 쓰고,
전역 random·시각·문자열 set 순회 순서에 기대지 않는다).
상수 주석의 "근거N"은 과제 브리프의 근거 번호(GENERATOR.md 표 참조), "가정"은 공개 근거 없이 정한 값.
이 생성기는 어떤 매처도 보지 않고 만들었다. 실제 DB처럼 보이게 하는 것이 목표다.
"""
import base64
import datetime as _dt
import math
import random
from bisect import bisect_right
from itertools import accumulate

# ---------------------------------------------------------------------------
# 0. 시나리오 기본값
# ---------------------------------------------------------------------------
DEFAULTS = {
    "ci_less_level": "base",      # low/base/high
    "email_share_level": "base",  # low/base/high
    "phone_change": 0.2,          # 뒤 레코드가 다른(옛/새) 번호를 쓸 확률
    "typo_level": "base",         # base/stress
    "foreign_share": 0.05,        # 외국인 투숙객 비율
}
REF_YEAR = 2026  # 가정: 나이 계산 기준 연도(시각을 읽지 않는다)

# ---------------------------------------------------------------------------
# 1. 사람·가구 구성
# ---------------------------------------------------------------------------
# 출생 연도 분포(성인 호텔 고객). 가정 — 1975~1990년생이 가장 많다고 봄
BIRTH_DECADE_W = [((1945, 1949), 0.02), ((1950, 1959), 0.07), ((1960, 1969), 0.16),
                  ((1970, 1979), 0.23), ((1980, 1989), 0.25), ((1990, 1999), 0.19),
                  ((2000, 2006), 0.08)]
# 배우자가 있을 확률(나이대별). 가정 — 통계청 혼인 상태 분포를 대략 따름
P_MARRIED_BY_AGE = [(29, 0.12), (39, 0.55), (49, 0.75), (64, 0.80), (200, 0.75)]
P_SPOUSE_IN_DATA = 0.45      # 가정: 기혼자의 배우자도 이 호텔 고객 DB에 있을 확률
P_CHILD_IN_DATA = 0.22       # 가정: 48세 이상이 성인 자녀와 함께 DB에 있을 확률
P_SIBLING_IN_DATA = 0.06     # 가정: 형제자매가 DB에 있을 확률
P_TWIN_ANCHOR = 0.012       # 근거7: 성인 고객의 1~2%가 쌍둥이가 되도록(가구 기준점 기준)
P_TWIN_SAME_SEX = 2 / 3      # 가정: 일란성+동성 이란성
P_TWIN_SHARE_SYLLABLE = 0.65  # 가정: 쌍둥이 이름이 한 음절을 공유(돌림자·짝 이름)
P_SIBLING_SHARE_SYLLABLE = 0.3  # 가정: 형제자매 돌림자
P_HOMONYM_SAME_DOB = 0.003   # 가정: 실제 수백만 DB 규모를 흉내 내 '동명이인·생일 같음' 쌍을 일부러 심음
P_NAME_CHANGE = 0.015        # 가정: 개명(연 십수만 건 규모) — 옛 레코드엔 옛 이름
P_NAME_CHANGE_OLD_ON_REC = 0.5  # 가정: 개명자의 CI 없는 레코드가 옛 이름일 확률
P_ENG_NICK = 0.07            # 가정: 영어 이름(David·Grace 등)을 쓰는 1965년 이후 출생자
P_LUNAR_FLAG_OLD = 0.30      # 가정: 1970년 전 출생자가 음력 생일을 대는 비율
P_LUNAR_FLAG_YOUNG = 0.04    # 가정: 그 뒤 출생자
P_LUNAR_ON_SELF_DOB = 0.6    # 가정: 음력파가 직접 적는 생일(프런트·전화예약)을 음력으로 적을 확률

# 외국인. 가정 — 롯데호텔 서울 외국인 고객 구성을 일본·중국 중심으로 봄
FOREIGN_CULTURE_W = [("JP", 0.33), ("CN", 0.27), ("WEST", 0.20), ("HKTW", 0.08),
                     ("SEA", 0.07), ("KDIAS", 0.05)]
P_FOREIGN_SPOUSE = 0.2       # 가정
P_FOREIGN_SPOUSE_SAME_SUR = 0.8  # 가정: 일본·서양·재외동포는 결혼 후 성을 바꾸는 경우가 많다
FOREIGN_PHONE_W = [("foreign", 0.55), ("korean", 0.15), ("none", 0.30)]  # 가정

# ---------------------------------------------------------------------------
# 2. 레코드 수와 채널(종류)
# ---------------------------------------------------------------------------
# 레코드 수는 '온라인 회원 계정(최대 1개, 근거1 DI)' + 'CI 없는 채널 레코드 n개'.
# n은 감마-포아송(음이항, 꼬리 두꺼움)으로 뽑는다. 근거9(dailypoint: 평균 2.3·14%가 4개 초과)
P_ONLINE = {"low": 0.95, "base": 0.88, "high": 0.72}       # 한국인이 온라인(본인인증) 회원일 확률 — 가정, 수준 조절용
EXTRA_MEAN = {"low": 0.42, "base": 0.98, "high": 1.70}     # CI 없는 채널 레코드 평균 개수 — 가정, 수준 조절용
EXTRA_SHAPE = 0.5            # 가정: 음이항 모양값(작을수록 꼬리 두꺼움)
EXTRA_MAX = 14               # 가정: 한 사람 최대 추가 레코드
# 오프라인 회원 계정이 '온라인 계정 등록'으로 본인인증을 거쳐 CI를 받았을 확률. 가정(근거2: 등록 절차는 있으나 CI 부착 여부 미확인)
OFFLINE_CI = {"low": 0.55, "base": 0.40, "high": 0.20}
EXTRA_KIND_W = [("offline_member", 0.40), ("sns_member", 0.25), ("non_member_stay", 0.35)]  # 가정
FOREIGN_FIRST_MEMBER = 0.65  # 가정: 외국인 첫 레코드가 회원 가입일 확률(아니면 OTA 투숙)
FOREIGN_EXTRA_MEAN = 0.8     # 가정
FOREIGN_EXTRA_KIND_W = [("foreign_member", 0.25), ("non_member_stay", 0.75)]  # 가정

# SNS 간편가입 제공자. 근거2(카카오·네이버·애플·구글). 비중은 가정
SNS_PROVIDER_W = [("kakao", 0.50), ("naver", 0.30), ("apple", 0.10), ("google", 0.10)]
# 비회원 투숙 채널. 가정
STAY_CHANNEL_W = [("domestic_ota", 0.30), ("global_ota", 0.45), ("direct", 0.25)]
P_STAY_BOOKED_BY_KIN = 0.18    # 근거8·2: 가족이 대신 예약해 연락처가 가족 것(가정 비율)
P_STAY_BOOKED_BY_FRIEND = 0.02  # 가정: 지인 대리예약
P_SAFE_NUMBER_DOMESTIC = 0.6   # 가정: 국내 OTA가 안심번호(0507-…)를 넘기는 비율
P_OTA_RELAY_EMAIL = 0.55       # 가정: 해외 OTA가 중계 이메일을 넘기는 비율
P_OTA_REAL_EMAIL = 0.30        # 가정: 실제 이메일(나머지는 없음)

# 레코드 종류별 값이 있을 확률. 가정(근거2: 프런트 생성 계정은 필드 완결성 미상)
OFFLINE_P = {"name_ko": 0.97, "name_en": 0.55, "phone": 0.85, "email": 0.35, "dob": 0.45}
DIRECT_STAY_P = {"name_ko": 0.95, "name_en": 0.25, "phone": 0.95, "email": 0.30, "dob": 0.10}
GLOBAL_OTA_P = {"name_ko": 0.10, "phone": 0.85, "dob": 0.03}
FOREIGN_MEMBER_P = {"email": 0.85, "dob": 0.60}
P_OFFLINE_EN_OWN = 0.5        # 가정: 프런트 영문명이 여권·카드 철자(아니면 직원이 한글을 로마자로)
P_DOB_PLACEHOLDER = 0.02      # 가정: 프런트가 생일 칸에 1900-01-01을 넣는 비율
P_KO_SPACE = 0.03             # 가정: '김 민준'처럼 성 뒤 띄어쓰기

# ---------------------------------------------------------------------------
# 3. 이메일·전화 공유와 변경
# ---------------------------------------------------------------------------
# 배우자 이메일 공유. 근거8(Pew 2014: 18–29세 12% → 65세 이상 47%). 30–49·50–64는 보간 가정
SPOUSE_SHARE_BASE_BY_AGE = [(29, 0.12), (49, 0.23), (64, 0.35), (200, 0.47)]
SPOUSE_SHARE_FLAT = {"low": 0.10, "high": 0.47}     # 브리프 지정값
FAMILY_SHARE = {"low": 0.15, "base": 0.30, "high": 0.60}  # 브리프 지정값(같은 성 가족, 가정을 다른 성 가족에도 적용)
SHARED_EMAIL_USE = 0.75       # 가정: 공유 주소를 쓰는 쪽이 레코드마다 그 주소를 적을 확률
FAMILY_EMAIL_USE = 0.6        # 가정: 자녀·동생이 부모·형 주소를 적을 확률
P_OLD_2G_NUMBER = 0.30        # 가정: 1975년 이전 출생자의 옛 번호가 011/016/017/019
P_NUMBER_REASSIGNED = 0.03    # 가정: 버린 옛 번호가 다른 고객에게 재할당되어 그 사람의 현재 번호가 됨

# ---------------------------------------------------------------------------
# 4. 입력 오류. base는 가정, stress는 근거10(GeCo: 속성당 10%, 레코드당 최대 2개)
# ---------------------------------------------------------------------------
TYPO_BASE = {
    # 본인인증 값(한글명·생일·전화)은 오류 없음, 직접 친 영문명·이메일만 소량
    "online_member": {"name_ko": 0.0, "name_en": 0.01, "phone": 0.0, "email": 0.005, "dob": 0.0},
    # 프런트 직원 수기 입력
    "offline_member": {"name_ko": 0.01, "name_en": 0.03, "phone": 0.015, "email": 0.03, "dob": 0.015},
    # 제공자가 넘긴 값 그대로
    "sns_member": {"name_ko": 0.0, "name_en": 0.0, "phone": 0.0, "email": 0.0, "dob": 0.0},
    # 예약자가 친 값
    "non_member_stay": {"name_ko": 0.01, "name_en": 0.03, "phone": 0.01, "email": 0.01, "dob": 0.01},
    "foreign_member": {"name_ko": 0.0, "name_en": 0.02, "phone": 0.01, "email": 0.01, "dob": 0.01},
}
STRESS_RATE = 0.10            # 근거10
MAX_CORRUPT_PER_RECORD = 2    # 근거10(GeCo 관행)
P_EMAIL_CASE_VARIANT = 0.04   # 가정: 직접 친 이메일의 대소문자 변형
P_EN_DEVIATION = 0.06         # 근거5: 본인 철자를 대체로 재사용, 가끔 한 음절·띄어쓰기가 바뀜

# ---------------------------------------------------------------------------
# 5. 성씨와 영문 표기. 근거3(2015 인구총조사 성씨 비중), 근거4(2007 여권 영문 성 통계)
# ---------------------------------------------------------------------------
SURNAMES = [
    ("김", 21.5, [("Kim", .993), ("Gim", .004), ("Ghim", .002), ("Kin", .001)]),
    ("이", 14.7, [("Lee", .985), ("Yi", .010), ("Rhee", .002), ("Rhie", .001), ("Ri", .001), ("Yee", .001)]),
    ("박", 8.4, [("Park", .965), ("Pak", .020), ("Bak", .010), ("Bahk", .005)]),
    ("최", 4.7, [("Choi", .931), ("Choe", .050), ("Choy", .012), ("Chey", .007)]),
    ("정", 4.4, [("Jung", .486), ("Jeong", .250), ("Chung", .200), ("Joung", .030), ("Jeung", .020), ("Chong", .014)]),
    ("강", 2.3, [("Kang", .90), ("Gang", .07), ("Kahng", .03)]),
    ("조", 2.1, [("Cho", .731), ("Jo", .200), ("Joe", .040), ("Jho", .029)]),
    ("윤", 2.1, [("Yoon", .489), ("Yun", .450), ("Youn", .061)]),
    ("장", 2.0, [("Jang", .60), ("Chang", .37), ("Jhang", .03)]),
    ("임", 1.7, [("Lim", .65), ("Im", .20), ("Yim", .10), ("Rim", .05)]),
    ("한", 1.5, [("Han", .97), ("Hahn", .03)]),
    ("오", 1.5, [("Oh", .97), ("O", .03)]),
    ("서", 1.5, [("Seo", .60), ("Suh", .35), ("Su", .03), ("Sur", .02)]),
    ("신", 1.4, [("Shin", .917), ("Sin", .060), ("Shinn", .023)]),
    ("권", 1.4, [("Kwon", .93), ("Gwon", .05), ("Kwun", .02)]),
    ("황", 1.4, [("Hwang", .99), ("Whang", .01)]),
    ("안", 1.4, [("Ahn", .85), ("An", .15)]),
    ("송", 1.3, [("Song", 1.0)]),
    ("유", 1.2, [("Yoo", .426), ("Yu", .400), ("You", .150), ("Ryu", .024)]),
    ("류", 0.6, [("Ryu", .50), ("Yoo", .20), ("Yu", .15), ("Rhyu", .08), ("Lyu", .07)]),
    ("전", 1.1, [("Jeon", .60), ("Chun", .30), ("Jun", .08), ("Chon", .02)]),
    ("홍", 1.1, [("Hong", 1.0)]),
    ("고", 0.9, [("Ko", .70), ("Go", .28), ("Koh", .02)]),
    ("문", 0.9, [("Moon", .90), ("Mun", .10)]),
    ("양", 0.9, [("Yang", 1.0)]),
    ("손", 0.9, [("Son", .95), ("Sohn", .05)]),
    ("배", 0.8, [("Bae", .90), ("Pae", .05), ("Bai", .05)]),
    ("백", 0.7, [("Baek", .70), ("Paik", .15), ("Paek", .10), ("Back", .05)]),
    ("허", 0.7, [("Heo", .55), ("Huh", .40), ("Hur", .05)]),
    ("남", 0.5, [("Nam", 1.0)]),
    ("심", 0.5, [("Shim", .80), ("Sim", .20)]),
    ("노", 0.6, [("Noh", .50), ("Roh", .30), ("No", .15), ("Rho", .05)]),
    ("하", 0.5, [("Ha", 1.0)]),
    ("곽", 0.4, [("Kwak", .90), ("Gwak", .10)]),
    ("성", 0.4, [("Sung", .60), ("Seong", .35), ("Seung", .05)]),
    ("차", 0.4, [("Cha", 1.0)]),
    ("주", 0.4, [("Joo", .60), ("Ju", .30), ("Chu", .10)]),
    ("우", 0.4, [("Woo", .90), ("Wu", .10)]),
    ("구", 0.4, [("Koo", .60), ("Ku", .25), ("Gu", .15)]),
    ("민", 0.3, [("Min", 1.0)]),
    ("진", 0.3, [("Jin", .90), ("Chin", .10)]),
    ("나", 0.3, [("Na", .95), ("Ra", .05)]),
    ("지", 0.3, [("Ji", .80), ("Chi", .20)]),
    ("엄", 0.3, [("Eom", .50), ("Um", .45), ("Uhm", .05)]),
    ("채", 0.3, [("Chae", .90), ("Chai", .10)]),
    ("원", 0.3, [("Won", 1.0)]),
    ("천", 0.2, [("Cheon", .50), ("Chun", .50)]),
    ("방", 0.2, [("Bang", .90), ("Pang", .10)]),
    ("공", 0.2, [("Kong", .80), ("Gong", .20)]),
    ("현", 0.2, [("Hyun", .70), ("Hyeon", .30)]),
    ("함", 0.2, [("Ham", 1.0)]),
    ("변", 0.2, [("Byun", .70), ("Byeon", .30)]),
    ("염", 0.2, [("Yeom", .60), ("Youm", .30), ("Yum", .10)]),
    ("여", 0.2, [("Yeo", .70), ("Yuh", .20), ("Yoh", .10)]),
    ("추", 0.1, [("Choo", .70), ("Chu", .30)]),
    ("도", 0.1, [("Do", .90), ("Doh", .10)]),
    ("석", 0.1, [("Seok", .60), ("Suk", .40)]),
    ("선", 0.1, [("Sun", .60), ("Seon", .40)]),
    ("설", 0.1, [("Seol", .60), ("Sul", .40)]),
    ("마", 0.1, [("Ma", 1.0)]),
    ("길", 0.1, [("Gil", .60), ("Kil", .40)]),
    ("연", 0.1, [("Yeon", .60), ("Youn", .30), ("Yun", .10)]),
    ("위", 0.1, [("Wi", .60), ("Wee", .40)]),
    ("표", 0.1, [("Pyo", 1.0)]),
    ("명", 0.1, [("Myung", .70), ("Myeong", .30)]),
    ("기", 0.1, [("Ki", .70), ("Gi", .30)]),
    ("반", 0.1, [("Ban", .80), ("Pan", .20)]),
    ("왕", 0.05, [("Wang", 1.0)]),
    ("금", 0.05, [("Keum", .60), ("Geum", .30), ("Kum", .10)]),
    ("옥", 0.05, [("Ok", 1.0)]),
    ("육", 0.05, [("Yook", .60), ("Yuk", .40)]),
    ("인", 0.05, [("In", 1.0)]),
    ("맹", 0.05, [("Maeng", 1.0)]),
    ("제", 0.05, [("Je", .70), ("Jae", .30)]),
    ("모", 0.05, [("Mo", 1.0)]),
    ("탁", 0.05, [("Tak", 1.0)]),
    ("국", 0.05, [("Kook", .60), ("Guk", .40)]),
    # 두 글자 성. 근거3(극소 비율)
    ("남궁", 0.04, [("Namgung", .60), ("Namkoong", .30), ("Namkung", .10)]),
    ("황보", 0.02, [("Hwangbo", 1.0)]),
    ("제갈", 0.01, [("Jegal", .70), ("Chegal", .30)]),
    ("선우", 0.01, [("Sunwoo", .70), ("Seonwoo", .20), ("Seonu", .10)]),
    ("독고", 0.005, [("Dokgo", .60), ("Tokko", .40)]),
]

# ---------------------------------------------------------------------------
# 6. 이름. 근거6(연대별 흔한 이름 + 긴 꼬리)
# ---------------------------------------------------------------------------
POPULAR = {
    (1950, "M"): "영수 영호 영식 영철 정수 정호 영길 종수 성수 용수",
    (1950, "F"): "영자 정자 순자 춘자 경자 옥순 말순 정숙 영숙 명숙",
    (1960, "M"): "영수 성수 영호 영철 정호 성호 상철 성진 종호 영진",
    (1960, "F"): "미숙 정숙 영숙 경숙 영희 명숙 순자 미경 경희 정희",
    (1970, "M"): "정훈 성호 성훈 상훈 성민 지훈 정호 영호 동현 상현",
    (1970, "F"): "은주 미경 은정 미영 현주 지영 은경 정은 미정 혜진",
    (1980, "M"): "지훈 성민 현우 정훈 성진 동현 준호 민수 상현 현준",
    (1980, "F"): "지혜 지영 수진 현주 은지 지은 혜진 미영 은영 정은",
    (1990, "M"): "지훈 동현 현우 성민 민수 준호 민준 승현 준영 도현",
    (1990, "F"): "유진 민지 수진 지은 지현 서연 지혜 수빈 은지 혜진",
    (2000, "M"): "민준 현우 지훈 동현 준호 민재 건우 승민 우진 성민",
    (2000, "F"): "서연 민지 유진 수빈 지민 지원 서현 예은 수민 민서",
}
P_POPULAR_NAME = 0.30         # 근거6 + 가정: 연대 상위 10개 이름에서 뽑는 비율
P_ONE_SYLLABLE = 0.01         # 가정: 외자 이름(혁·준 등)
POOLS = {  # (시대, 성별) -> (첫 음절, 둘째 음절). 앞쪽일수록 흔함(가정)
    ("old", "M"): ("영 정 성 종 상 용 병 재 동 광 승 기 만 문 진 창 태 형 한 석 길 춘 덕",
                   "수 호 식 철 길 진 남 근 석 기 환 일 원 택 섭 규 현 우 준 배 복 구"),
    ("mid", "M"): ("정 성 상 동 지 민 준 현 재 승 영 진 태 경 대 종 용 우 형 기",
                   "훈 호 민 현 수 진 우 철 석 준 원 기 혁 환 일 규 영 욱 범 섭"),
    ("young", "M"): ("민 지 현 준 도 서 승 우 건 시 예 주 하 태 유 은 재 성 동 윤",
                     "준 훈 우 현 호 민 진 성 혁 원 재 빈 윤 율 환 수 결 찬 후"),
    ("old", "F"): ("영 정 순 미 경 명 춘 옥 금 숙 은 혜 선 말 화 인 희 길",
                   "자 숙 희 순 옥 미 경 애 선 화 영 란 례 분 남 연"),
    ("mid", "F"): ("은 미 지 수 현 혜 정 유 선 소 윤 민 나 효 보 진 연 희",
                   "주 경 정 영 진 희 연 은 숙 미 아 선 지 현 란 혜"),
    ("young", "F"): ("서 지 민 수 유 예 하 채 윤 다 소 은 시 나 아 가 연",
                     "연 은 민 진 윤 아 현 빈 원 서 영 희 린 우 인 율 별"),
}
ONE_SYL = {"M": "혁 준 훈 건 철 진 현 산 용 결", "F": "란 희 진 숙 아 은 별 솔 미 영"}
KAKAO_NICKS = "행복한하루 하늘 바다 새벽 봄날 산들바람 푸른하늘 별빛 커피한잔 여행가 맑음 해피 사랑 꿈꾸는 나무 햇살 달빛 고양이 강아지 초록".split()
ENG_NICK = {"M": "David Daniel James John Paul Peter Andrew Michael Brian Kevin Steve Chris".split(),
            "F": "Grace Jenny Sarah Julia Christine Esther Rachel Amy Jane Sophia Grace Hannah".split()}
EMAIL_WORDS = "happy sky blue angel lovely sunny dream star moon jjang smile luna ocean rain mint cool sweet".split()

# 한국 이메일 도메인(출생 연대별). 가정(국내 포털 점유율 상식)
KR_DOMAINS = {
    "old": [("naver.com", .40), ("hanmail.net", .30), ("daum.net", .10), ("nate.com", .08),
            ("gmail.com", .07), ("hotmail.com", .03), ("kakao.com", .02)],
    "mid": [("naver.com", .45), ("gmail.com", .20), ("hanmail.net", .15), ("daum.net", .07),
            ("nate.com", .08), ("hotmail.com", .03), ("kakao.com", .02)],
    "young": [("naver.com", .45), ("gmail.com", .40), ("kakao.com", .05), ("daum.net", .04),
              ("hanmail.net", .02), ("nate.com", .02), ("icloud.com", .02)],
}
DOMAIN_TYPOS = {
    "naver.com": ["naver.con", "naver.co", "nave.com", "navr.com", "nver.com", "naver.cm"],
    "gmail.com": ["gmail.con", "gmial.com", "gmai.com", "gmail.co", "gamil.com"],
    "hanmail.net": ["hanmail.ent", "hanmial.net", "hanmail.com", "hanmail.nte"],
    "daum.net": ["duam.net", "daum.ner", "daum.com"],
    "nate.com": ["nate.con", "nat.com"],
}

# 영문명 쓰기 방식. 가정
GIVEN_JOIN_OWN = [("concat", .45), ("hyphen", .25), ("space", .22), ("camel", .08)]
GIVEN_JOIN_DESK = [("concat", .50), ("space", .40), ("hyphen", .10)]
EN_STYLE_OWN = [("GS_title", .40), ("SG_upper", .30), ("SG_title", .15), ("GS_upper", .10), ("lower", .05)]
EN_STYLE_OTA = [("SG_upper", .33), ("GS_upper", .30), ("GS_title", .20), ("SG_title", .10),
                ("lower", .04), ("GDS", .03)]
EN_STYLE_DESK = [("SG_upper", .50), ("SG_title", .30), ("GS_title", .15), ("SG_comma", .05)]
P_KEEP_OWN_STYLE = 0.75       # 가정: 직접 칠 때 평소 쓰는 순서·대소문자를 유지
P_DESK_RR = 0.6               # 가정: 직원이 로마자 표기법(RR)으로, 나머지는 흔한 관용 철자로

# 전화 표기. 가정(시스템별 저장 형식)
PHONE_FMT_OFFLINE = [("dash", .60), ("plain", .30), ("space", .05), ("dot", .03), ("paren", .02)]
PHONE_FMT_OTA = [("intl_plain", .30), ("intl_space", .25), ("plain", .25), ("dash", .15), ("intl_dash", .05)]
PHONE_FMT_DIRECT = [("dash", .60), ("plain", .40)]
PHONE_FMT_FOREIGNER_KR = [("dash", .50), ("intl_dash", .30), ("plain", .20)]

# ---------------------------------------------------------------------------
# 외국인 이름 자료(라틴 문자). 각 항목의 첫 철자가 본인 철자일 확률이 높다(가정)
# ---------------------------------------------------------------------------
FOREIGN = {
    "JP": {
        "sur": ["Sato|Satou|Satoh", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito|Itou|Itoh",
                "Yamamoto", "Nakamura", "Kobayashi", "Kato|Katou|Katoh", "Yoshida", "Yamada",
                "Sasaki", "Saito|Saitou|Saitoh", "Matsumoto", "Inoue", "Kimura", "Hayashi",
                "Shimizu", "Ota|Ohta|Oota"],
        "M": ["Haruto", "Hiroshi", "Takeshi", "Kenji", "Daiki", "Yuto|Yuuto", "Sota|Souta", "Ren",
              "Kazuki", "Shota|Shouta", "Yusuke|Yuusuke", "Ryo|Ryou", "Shohei|Shouhei", "Takuya"],
        "F": ["Yui", "Yuki", "Aoi", "Sakura", "Hina", "Mei", "Akiko", "Yoko|Youko", "Keiko",
              "Naoko", "Yuko|Yuuko", "Mayu", "Rina", "Emi"],
        "order_sg": 0.4, "cc": [("81", 1.0)],
        "domains": [("gmail.com", .35), ("yahoo.co.jp", .25), ("docomo.ne.jp", .15), ("icloud.com", .15), ("outlook.jp", .10)],
    },
    "CN": {
        "sur": ["Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Zhao", "Wu", "Zhou", "Xu",
                "Sun", "Ma", "Zhu", "Hu", "Guo", "He", "Lin", "Gao", "Luo"],
        "M": ["Wei", "Qiang", "Lei", "Jun", "Yang", "Jie", "Tao", "Ming", "Hao", "Xiao Ming",
              "Hao Ran", "Yu Xuan", "Zi Hao", "Jian Guo", "Zhi Wei"],
        "F": ["Fang", "Na", "Min", "Jing", "Li", "Yan", "Xin", "Yi", "Xiao Hong", "Zi Han",
              "Jia Hui", "Meng Yao", "Yu Tong", "Xin Yi"],
        "order_sg": 0.6, "cc": [("86", 1.0)],
        "domains": [("qq.com", .35), ("163.com", .25), ("126.com", .10), ("gmail.com", .20), ("sina.com", .10)],
    },
    "HKTW": {
        "sur": ["Chan", "Wong", "Cheung", "Lau", "Ng", "Leung", "Lee", "Chen", "Lin", "Tsai", "Huang", "Chang"],
        "M": ["Ka Ho", "Wai Man", "Chi Keung", "Chia Hao", "Kwok Wing", "Chun Kit", "Yu Hsiang"],
        "F": ["Mei Ling", "Ya Ting", "Yu Ting", "Wing Yan", "Hsin Yi", "Siu Ling", "Pei Shan"],
        "eng": {"M": ["Kelvin", "Jason", "Eric", "Kenneth", "Alan", "Vincent"],
                "F": ["Ivy", "Cindy", "Wendy", "Karen", "Winnie", "Joyce"]},
        "order_sg": 0.5, "cc": [("852", .5), ("886", .5)],
        "domains": [("gmail.com", .45), ("yahoo.com.hk", .20), ("hotmail.com", .20), ("yahoo.com.tw", .15)],
    },
    "WEST": {
        "sur": ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Taylor",
                "Anderson", "Thomas", "Martin", "Garcia", "Martinez", "Mueller|Müller|Muller", "Schmidt",
                "Dubois", "Rossi", "O'Brien|OBrien|O Brien", "MacDonald|Macdonald|McDonald"],
        "M": ["James", "John", "Robert", "Michael", "David", "William", "Daniel", "Thomas", "Matthew",
              "Christopher|Chris", "Alexander|Alex", "Pierre", "Luca", "Stefan"],
        "F": ["Mary", "Jennifer", "Linda", "Sarah", "Emma", "Olivia", "Anna", "Laura",
              "Elizabeth|Liz", "Katherine|Catherine|Kate", "Sophie", "Giulia", "Hannah"],
        "order_sg": 0.05, "cc": [("1", .45), ("44", .15), ("49", .15), ("33", .10), ("61", .15)],
        "domains": [("gmail.com", .45), ("outlook.com", .15), ("yahoo.com", .15), ("hotmail.com", .15), ("icloud.com", .10)],
    },
    "SEA": {
        "sur": ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Srisawat", "Chaiyaporn", "Wongsakul",
                "Santos", "Reyes", "Cruz", "Bautista"],
        "M": ["Van An", "Duc Minh", "Quoc Bao", "Somchai", "Nattapong", "Jose", "Mark"],
        "F": ["Thi Mai", "Thi Lan", "Minh Anh", "Supaporn", "Kanokwan", "Maria", "Angelica"],
        "order_sg": 0.5, "cc": [("84", .45), ("66", .30), ("63", .25)],
        "domains": [("gmail.com", .70), ("yahoo.com", .20), ("outlook.com", .10)],
    },
    "KDIAS": {  # 재외동포(외국 국적): 한국 성 + 서양 이름, 한글명·CI 없음 — 일부러 넣은 어려운 경우
        "sur": ["Kim", "Lee", "Park", "Choi", "Jung|Chung", "Kang", "Cho", "Yoon"],
        "M": ["Daniel", "David", "Andrew", "Brian", "Justin", "Paul", "Eugene"],
        "F": ["Grace", "Esther", "Christine", "Jennifer", "Michelle", "Sarah", "Jenny"],
        "order_sg": 0.1, "cc": [("1", .8), ("61", .2)],
        "domains": [("gmail.com", .60), ("yahoo.com", .15), ("outlook.com", .15), ("icloud.com", .10)],
    },
}
P_WEST_MIDDLE = 0.35          # 가정: 서양인 중간 이름 이니셜
P_MIDDLE_ON_REC = 0.5         # 가정: 레코드에 중간 이니셜이 찍히는 비율
P_HK_ENG_NAME = 0.4           # 가정: 홍콩·대만 고객의 영어 이름 사용
P_HK_ENG_ON_REC = 0.6

# ===========================================================================
# 내부 도구
# ===========================================================================
class _WT:
    """가중치 표에서 하나 고르기(누적합 + 이분 탐색)."""
    __slots__ = ("vals", "cum", "total")

    def __init__(self, items):
        self.vals = [v for v, _ in items]
        self.cum = list(accumulate(w for _, w in items))
        self.total = self.cum[-1]

    def pick(self, rng):
        i = bisect_right(self.cum, rng.random() * self.total)
        return self.vals[min(i, len(self.vals) - 1)]

    def modal(self):
        best, bw, prev = self.vals[0], -1.0, 0.0
        for v, c in zip(self.vals, self.cum):
            if c - prev > bw:
                best, bw = v, c - prev
            prev = c
        return best


def _zipf(items, s=0.8):
    return _WT([(v, 1.0 / (i + 1) ** s) for i, v in enumerate(items)])


_SUR_W = _WT([(k, w) for k, w, _ in SURNAMES])
_SUR_SPELL = {k: _WT(sp) for k, _, sp in SURNAMES}
_POPULAR_W = {k: _zipf(v.split()) for k, v in POPULAR.items()}
_POOL_W = {k: (_zipf(a.split(), 0.6), _zipf(b.split(), 0.6)) for k, (a, b) in POOLS.items()}
_ONE_SYL_W = {k: _zipf(v.split(), 0.5) for k, v in ONE_SYL.items()}
_KR_DOM_W = {k: _WT(v) for k, v in KR_DOMAINS.items()}
_BIRTH_YEAR_W = _WT([(y, w / (b - a + 1)) for (a, b), w in BIRTH_DECADE_W for y in range(a, b + 1)])
_W = {name: _WT(items) for name, items in {
    "join_own": GIVEN_JOIN_OWN, "join_desk": GIVEN_JOIN_DESK, "style_own": EN_STYLE_OWN,
    "style_ota": EN_STYLE_OTA, "style_desk": EN_STYLE_DESK, "fmt_off": PHONE_FMT_OFFLINE,
    "fmt_ota": PHONE_FMT_OTA, "fmt_direct": PHONE_FMT_DIRECT, "fmt_fkr": PHONE_FMT_FOREIGNER_KR,
    "extra_kind": EXTRA_KIND_W, "f_extra_kind": FOREIGN_EXTRA_KIND_W, "sns": SNS_PROVIDER_W,
    "stay": STAY_CHANNEL_W, "culture": FOREIGN_CULTURE_W, "f_phone": FOREIGN_PHONE_W,
}.items()}


def _era(by):
    return "old" if by < 1970 else ("mid" if by < 1990 else "young")


def _decade(by):
    return max(1950, min(2000, by // 10 * 10))


def _age(by):
    return REF_YEAR - by


def _by_age(table, age):
    for lim, v in table:
        if age <= lim:
            return v
    return table[-1][1]


def _poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def _negbin(rng, mean, shape=EXTRA_SHAPE, cap=EXTRA_MAX):
    if mean <= 0:
        return 0
    return min(cap, _poisson(rng, rng.gammavariate(shape, mean / shape)))


# ---------------------------------------------------------------------------
# 한글 자모와 로마자 표기
# ---------------------------------------------------------------------------
CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
JONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")
INIT_RR = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
VOW_RR = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo",
          "we", "wi", "yu", "eu", "ui", "i"]
FIN_RR = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "p", "l", "l", "p", "l", "m", "p", "p",
          "t", "t", "ng", "t", "t", "k", "t", "p", "t"]


def _decomp(ch):
    code = ord(ch) - 0xAC00
    if 0 <= code < 11172:
        return code // 588, (code % 588) // 28, code % 28
    return None


def _comp(c, v, f):
    return chr(0xAC00 + c * 588 + v * 28 + f)


_SYL_CACHE = {}


def _syl_opts(ch):
    """음절 하나의 (초성 표, 중성 표, 종성 표, RR 문자열). 근거4(성 표기 분포)·근거4 이름 변이 예시를 일반화."""
    if ch in _SYL_CACHE:
        return _SYL_CACHE[ch]
    c, v, f = _decomp(ch)
    i_opts = [(INIT_RR[c], 1.0)]
    if c == 0:
        i_opts = [("k", .55), ("g", .45)]          # 경 Kyung/Gyeong, 기 Ki/Gi
    elif c == 3:
        i_opts = [("d", .85), ("t", .15)]
    elif c == 7:
        i_opts = [("b", .75), ("p", .25)]          # 병 Byung/Pyung
    elif c == 12:
        i_opts = [("j", .92), ("ch", .08)]
    elif c == 5:
        i_opts = [("r", .6), ("l", .4)]            # 린 Rin/Lin
    elif c == 9 and v in (2, 6, 12, 17, 20):
        i_opts = [("sh", .55), ("s", .45)]         # 시 Shi/Si, 신 Shin/Sin
    v_opts = [(VOW_RR[v], 1.0)]
    if v == 13:   # ㅜ
        if c == 11:
            v_opts = [("woo", .60), ("wu", .25), ("u", .15)]   # 우 Woo/Wu/U
        else:
            v_opts = [("oo", .50), ("u", .45), ("ou", .05)]    # 준 Jun/Joon, 수 Su/Soo
    elif v == 6:  # ㅕ
        if f == 21:
            v_opts = [("you", .45), ("yeo", .30), ("yo", .15), ("yu", .10)]  # 영 Young/Yeong/Yong/Yung
        elif f == 4:
            v_opts = [("yu", .50), ("yeo", .40), ("you", .10)]               # 현 Hyun/Hyeon/Hyoun
        else:
            v_opts = [("yeo", .50), ("yu", .30), ("yo", .20)]
    elif v == 4:  # ㅓ
        v_opts = [("u", .55), ("eo", .40), ("o", .05)] if f == 21 else [("eo", .45), ("u", .45), ("o", .10)]
    elif v == 18:  # ㅡ
        v_opts = [("eu", .70), ("u", .30)] if c == 11 else [("eu", .55), ("u", .40), ("e", .05)]
    elif v == 19:  # ㅢ
        v_opts = [("ee", .70), ("ui", .20), ("i", .10)] if c == 18 else [("ui", .60), ("ee", .20), ("i", .20)]
    elif v == 20:
        v_opts = [("i", .92), ("ee", .08)]
    elif v == 1:
        v_opts = [("ae", .90), ("ai", .05), ("e", .05)]
    elif v == 17:
        v_opts = [("yu", .60), ("yoo", .30), ("you", .10)]
    elif v == 11:
        v_opts = [("oe", .50), ("oi", .30), ("we", .20)]
    elif v == 16:
        v_opts = [("wi", .70), ("wee", .30)]
    f_opts = [(FIN_RR[f], 1.0)]
    if f == 1:
        f_opts = [("k", .9), ("g", .1)]
    elif f == 17:
        f_opts = [("p", .5), ("b", .5)]
    out = (_WT(i_opts), _WT(v_opts), _WT(f_opts),
           INIT_RR[c] + VOW_RR[v] + FIN_RR[f])
    _SYL_CACHE[ch] = out
    return out


def _rom_syl(rng, ch, mode):
    i, v, f, rr = _syl_opts(ch)
    if mode == "rr":
        return rr
    if mode == "modal":
        return i.modal() + v.modal() + f.modal()
    return i.pick(rng) + v.pick(rng) + f.pick(rng)


def _join_given(parts, style):
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0].capitalize()
    if style == "hyphen":
        return parts[0].capitalize() + "-" + "-".join(parts[1:])
    if style == "space":
        return " ".join(p.capitalize() for p in parts)
    if style == "camel":
        return "".join(p.capitalize() for p in parts)
    s = "".join(parts)
    return s[0].upper() + s[1:]


def _render_en(sur, given, style):
    if not given:
        return sur.upper() if "upper" in style else sur
    if not sur:
        return given
    if style == "GS_title":
        return f"{given} {sur}"
    if style == "SG_title":
        return f"{sur} {given}"
    if style == "SG_upper":
        return f"{sur} {given}".upper()
    if style == "GS_upper":
        return f"{given} {sur}".upper()
    if style == "lower":
        return f"{given} {sur}".lower()
    if style == "SG_comma":
        return f"{sur}, {given}"
    if style == "GDS":
        return f"{sur.upper()}/{given.upper()}"
    return f"{given} {sur}"


def _variant_pick(rng, spec, p_first=0.8):
    vs = spec.split("|")
    if len(vs) == 1 or rng.random() < p_first:
        return vs[0]
    return rng.choice(vs[1:])


# ---------------------------------------------------------------------------
# 전화 표기와 정규화
# ---------------------------------------------------------------------------
def _fmt_kr(d, style):
    """d: '01012345678'(11자리) 또는 '0111234567'(10자리)."""
    if len(d) == 11:
        a, b, c = d[:3], d[3:7], d[7:]
    elif len(d) == 12:  # 0507 안심번호
        a, b, c = d[:4], d[4:8], d[8:]
    else:
        a, b, c = d[:3], d[3:6], d[6:]
    rest = d[1:]
    if style == "dash":
        return f"{a}-{b}-{c}"
    if style == "plain":
        return d
    if style == "space":
        return f"{a} {b} {c}"
    if style == "dot":
        return f"{a}.{b}.{c}"
    if style == "paren":
        return f"({a}){b}-{c}"
    if style == "intl_dash":
        return f"+82 {a[1:]}-{b}-{c}"
    if style == "intl_plain":
        return "+82" + rest
    if style == "intl_space":
        return f"+82 {a[1:]} {b} {c}"
    return f"{a}-{b}-{c}"


# 국가별 휴대전화 묶음(국제 형식). 가정(각국 관행)
_FOREIGN_GROUPS = {"81": (2, 4, 4), "86": (3, 4, 4), "852": (4, 4), "886": (3, 3, 3), "44": (4, 6),
                   "49": (3, 8), "33": (1, 2, 2, 2, 2), "61": (3, 3, 3), "84": (2, 3, 4), "66": (2, 3, 4),
                   "63": (3, 3, 4)}


def _group(d, sizes):
    out, i = [], 0
    for n in sizes:
        if i >= len(d):
            break
        out.append(d[i:i + n])
        i += n
    if i < len(d):
        out[-1] += d[i:]
    return " ".join(out)


def norm_phone(s):
    """비교용 정규화(생성기 내부 충돌 검사와 selfcheck 공용). 매칭 규칙이 아니다."""
    if not s:
        return ""
    dg = "".join(ch for ch in s if ch.isdigit())
    t = s.strip()
    if (t.startswith("+82") or t.startswith("82")) and len(dg) >= 11:
        dg = "0" + dg[2:]
    return dg


def norm_email(s):
    return s.strip().lower() if s else ""


# ---------------------------------------------------------------------------
# 오타 함수. 근거10(GeCo식: 자판 인접 치환·자리 바꿈·삽입·삭제·숫자 바꿈)
# ---------------------------------------------------------------------------
_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_ADJ = {}
for _r, _row in enumerate(_ROWS):
    for _i, _ch in enumerate(_row):
        nb = []
        if _i > 0:
            nb.append(_row[_i - 1])
        if _i + 1 < len(_row):
            nb.append(_row[_i + 1])
        for _rr in (_r - 1, _r + 1):
            if 0 <= _rr < 3:
                for _j in (_i - 1, _i, _i + 1) if _rr > _r else (_i, _i + 1):
                    if 0 <= _j < len(_ROWS[_rr]):
                        nb.append(_ROWS[_rr][_j])
        _ADJ[_ch] = "".join(nb)
_DIGIT_ADJ = {"0": "9", "1": "24", "2": "135", "3": "26", "4": "157", "5": "2468", "6": "359",
              "7": "48", "8": "579", "9": "680"}


def _latin_typo(rng, s):
    pos = [i for i, ch in enumerate(s) if ch.isalnum()]
    if len(pos) < 2:
        return s
    op = rng.random()
    i = rng.choice(pos)
    ch = s[i]
    if op < 0.4:  # 인접 키 치환
        lo = ch.lower()
        pool = _ADJ.get(lo) or _DIGIT_ADJ.get(lo) or "e"
        new = rng.choice(pool)
        new = new.upper() if ch.isupper() else new
        return s[:i] + new + s[i + 1:]
    if op < 0.6:  # 자리 바꿈
        j = i + 1 if i + 1 < len(s) and s[i + 1].isalnum() else i - 1
        if j < 0 or not s[j].isalnum() or s[i] == s[j]:
            return s[:i] + s[i + 1:]
        a, b = min(i, j), max(i, j)
        return s[:a] + s[b] + s[a] + s[b + 1:]
    if op < 0.8:  # 삭제
        return s[:i] + s[i + 1:]
    lo = ch.lower()  # 삽입(인접 키 또는 같은 글자 중복)
    new = rng.choice((_ADJ.get(lo) or lo) + lo)
    new = new.upper() if ch.isupper() else new
    return s[:i] + new + s[i:]


def _digit_typo(rng, s):
    pos = [i for i, ch in enumerate(s) if ch.isdigit()]
    # 맨 앞 국번(010·+82)은 덜 틀리게: 뒤쪽 숫자 위주
    pos = pos[3:] if len(pos) > 6 else pos
    if len(pos) < 2:
        return s
    op = rng.random()
    k = rng.randrange(len(pos))
    i = pos[k]
    if op < 0.5:
        new = rng.choice(_DIGIT_ADJ[s[i]]) if rng.random() < 0.6 else rng.choice("0123456789".replace(s[i], ""))
        return s[:i] + new + s[i + 1:]
    if op < 0.8 and k + 1 < len(pos):
        j = pos[k + 1]
        if s[i] != s[j]:
            lst = list(s)
            lst[i], lst[j] = lst[j], lst[i]
            return "".join(lst)
    return s[:i] + s[i + 1:]


def _dob_typo(rng, iso):
    try:
        d = _dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    for _ in range(6):
        op = rng.random()
        try:
            if op < 0.35 and d.day <= 12 and d.day != d.month:
                nd = d.replace(month=d.day, day=d.month)              # 월·일 뒤바꿈
            elif op < 0.6:
                nd = d.replace(year=d.year + rng.choice((-1, 1)))      # 연도 끝자리
            elif op < 0.85:
                dd = list(f"{d.day:02d}")
                k = rng.randrange(2)
                dd[k] = rng.choice(_DIGIT_ADJ[dd[k]])
                nd = d.replace(day=int("".join(dd)))                  # 일 숫자 하나
            else:
                nd = d.replace(year=d.year + rng.choice((-10, 10)))    # 연도 십의 자리
        except ValueError:
            continue
        if nd != d and 1900 < nd.year < 2010:
            return nd.isoformat()
    return iso


def _email_typo(rng, e):
    if "@" not in e:
        return _latin_typo(rng, e)
    h, dom = e.rsplit("@", 1)
    if rng.random() < 0.45:
        opts = DOMAIN_TYPOS.get(dom.lower())
        nd = rng.choice(opts) if opts else _latin_typo(rng, dom)
        if dom.isupper():
            nd = nd.upper()
        return h + "@" + nd
    return _latin_typo(rng, h) + "@" + dom


_CON_ADJ = {"ㅂ": "ㅈㅁㅃ", "ㅈ": "ㅂㄷㄴㅉ", "ㄷ": "ㅈㄱㅇㄸ", "ㄱ": "ㄷㅅㄹㄲ", "ㅅ": "ㄱㅎㅆ",
            "ㅁ": "ㅂㄴㅋ", "ㄴ": "ㅁㅇㅈㅌ", "ㅇ": "ㄴㄹㄷㅊ", "ㄹ": "ㅇㅎㄱㅍ", "ㅎ": "ㄹㅅ",
            "ㅋ": "ㅁㅌ", "ㅌ": "ㅋㅊㄴ", "ㅊ": "ㅌㅍㅇ", "ㅍ": "ㅊㄹ", "ㄲ": "ㄱ", "ㄸ": "ㄷ",
            "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
_VOW_CONF = {"ㅐ": "ㅔ", "ㅔ": "ㅐ", "ㅒ": "ㅖ", "ㅖ": "ㅒ", "ㅙ": "ㅚㅞ", "ㅚ": "ㅙㅞ", "ㅞ": "ㅙㅚ",
             "ㅢ": "ㅣ", "ㅓ": "ㅕㅗ", "ㅕ": "ㅓ", "ㅗ": "ㅓㅛ", "ㅛ": "ㅗ", "ㅜ": "ㅠㅡ", "ㅠ": "ㅜ",
             "ㅏ": "ㅑㅣ", "ㅣ": "ㅏ", "ㅡ": "ㅜ", "ㅑ": "ㅏ"}


def _ko_typo(rng, s):
    pos = [i for i, ch in enumerate(s) if _decomp(ch)]
    if not pos:
        return s
    # 성(첫 글자)보다 이름 쪽이 덜 익숙해 더 틀린다(가정)
    cand = pos[1:] if len(pos) > 1 and rng.random() < 0.8 else pos
    i = rng.choice(cand)
    c, v, f = _decomp(s[i])
    op = rng.random()
    if op < 0.35 and JUNG[v] in _VOW_CONF:
        v = JUNG.index(rng.choice(_VOW_CONF[JUNG[v]]))
    elif op < 0.6 and CHO[c] in _CON_ADJ:
        c = CHO.index(rng.choice(_CON_ADJ[CHO[c]]))
    elif op < 0.85:
        if f == 0:
            f = JONG.index(rng.choice("ㄴㅇㄹ"))
        elif f == 4:
            f = 21
        elif f == 21:
            f = 4 if rng.random() < 0.6 else 0
        else:
            f = 0
    elif op < 0.95 and len(pos) >= 3:
        j = pos[-1] if i != pos[-1] else pos[-2]
        a, b = min(i, j), max(i, j)
        lst = list(s)
        lst[a], lst[b] = lst[b], lst[a]
        return "".join(lst)
    else:
        if len(pos) >= 3:
            return s[:i] + s[i + 1:]
        f = 0 if f else JONG.index("ㄴ")
    return s[:i] + _comp(c, v, f) + s[i + 1:]


_CORRUPT = {"name_ko": _ko_typo, "name_en": _latin_typo, "phone": _digit_typo,
            "email": _email_typo, "dob": _dob_typo}
_ATTRS = ("name_ko", "name_en", "phone", "email", "dob")


# ===========================================================================
# 생성기 본체
# ===========================================================================
class _Gen:
    def __init__(self, seed, scenario):
        sc = dict(DEFAULTS)
        sc.update(scenario or {})
        for k, allowed in (("ci_less_level", ("low", "base", "high")),
                           ("email_share_level", ("low", "base", "high")),
                           ("typo_level", ("base", "stress"))):
            if sc[k] not in allowed:
                raise ValueError(f"{k}={sc[k]!r}, 허용값 {allowed}")
        sc["phone_change"] = float(sc["phone_change"])
        sc["foreign_share"] = float(sc["foreign_share"])
        if not (0 <= sc["phone_change"] <= 1 and 0 <= sc["foreign_share"] <= 1):
            raise ValueError("phone_change·foreign_share는 0~1")
        self.sc = sc
        self.rng = random.Random(seed)
        self.P = []
        self.links = {}
        self.used_phone = set()
        self.used_email = set()
        self.used_ci = set()
        self.phone_owner = {}   # 정규화 번호 -> 주인 idx 목록
        self.email_owner = {}
        self.hh_count = 0
        self.friend_used = set()
        self.reassign_targets = set()

    # ---- 링크 -------------------------------------------------------------
    def link(self, a, b, t):
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        if key not in self.links:
            self.links[key] = t

    def linked(self, a, b):
        return ((a, b) if a < b else (b, a)) in self.links

    # ---- 고유값 풀 ---------------------------------------------------------
    def _own_phone(self, idx, digits):
        self.phone_owner.setdefault(norm_phone(digits), []).append(idx)

    def _own_email(self, idx, e):
        self.email_owner.setdefault(norm_email(e), []).append(idx)

    def new_kr_mobile(self, old_2g=False):
        rng = self.rng
        while True:
            if old_2g:
                d = rng.choice(("011", "011", "016", "017", "019")) + str(rng.randint(200, 999)) + f"{rng.randrange(10000):04d}"
            else:
                d = "010" + str(rng.randint(2000, 9999)) + f"{rng.randrange(10000):04d}"
            if d not in self.used_phone:
                self.used_phone.add(d)
                return d

    def new_safe_number(self):
        rng = self.rng
        while True:
            d = "0507" + str(rng.randint(1000, 9999)) + f"{rng.randrange(10000):04d}"
            if d not in self.used_phone:
                self.used_phone.add(d)
                return d

    def new_foreign_phone(self, cc):
        rng = self.rng
        lens = {"81": ("90", 8), "86": ("13", 9), "852": ("9", 7), "886": ("9", 8), "1": ("", 10),
                "44": ("7", 9), "49": ("15", 9), "33": ("6", 8), "61": ("4", 8), "84": ("9", 8),
                "66": ("8", 8), "63": ("9", 9)}
        pre, n = lens.get(cc, ("", 9))
        while True:
            if cc == "1":
                nat = str(rng.randint(201, 989)) + str(rng.randint(200, 999)) + f"{rng.randrange(10000):04d}"
            else:
                nat = pre + "".join(rng.choice("0123456789") for _ in range(n))
            key = cc + nat
            if key not in self.used_phone:
                self.used_phone.add(key)
                return cc, nat

    def new_ci(self):
        while True:
            ci = base64.b64encode(self.rng.getrandbits(512).to_bytes(64, "big")).decode("ascii")
            if ci not in self.used_ci:
                self.used_ci.add(ci)
                return ci

    def _unique_email(self, handle, domain):
        rng = self.rng
        h = handle
        for _ in range(50):
            e = f"{h}@{domain}"
            if e not in self.used_email and len(h) >= 4:
                self.used_email.add(e)
                return e
            h = handle + str(rng.randint(1, 9999))
        raise RuntimeError("email pool exhausted")

    def kr_handle(self, p):
        rng = self.rng
        sur = p["sur_en"].lower().replace(" ", "").replace("'", "")
        giv = "".join(p["given_en"]).lower()
        ini = "".join(s[0] for s in p["given_en"] if s)
        yy = f"{p['dob'].year % 100:02d}"
        md = f"{p['dob'].month:02d}{p['dob'].day:02d}"
        r = rng.random()
        dig = rng.choice((yy, md, str(rng.randint(1, 99)), str(rng.randint(100, 9999)), "", ""))
        if p.get("eng_nick") and r < 0.08:
            return f"{p['eng_nick'].lower()}{rng.choice(('.', '', '_'))}{sur}{dig}"
        if r < 0.22:
            return f"{giv}{sur}{dig}"
        if r < 0.30:
            return f"{giv}.{sur}"
        if r < 0.45:
            return f"{sur}{giv}{dig}"
        if r < 0.60:
            return f"{ini}{sur}{dig or yy}"
        if r < 0.67:
            return f"{sur}{ini}{dig or yy}"
        if r < 0.77:
            return f"{giv}{dig or yy}"
        if r < 0.82:
            return f"{giv}_{sur}"
        return f"{rng.choice(EMAIL_WORDS)}{rng.choice(EMAIL_WORDS) if rng.random() < .3 else ''}{rng.choice(('1004', '7777', yy, md, str(rng.randint(1, 999))))}"

    def kr_email(self, p, domain=None):
        if domain is None:
            domain = _KR_DOM_W[_era(p["dob"].year)].pick(self.rng)
        e = self._unique_email(self.kr_handle(p), domain)
        self._own_email(p["idx"], e)
        return e

    def relay_email(self, idx, kind):
        rng = self.rng
        alnum = "abcdefghijklmnopqrstuvwxyz0123456789"
        rnd = "".join(rng.choice(alnum) for _ in range(10))
        dom = {"apple": "privaterelay.appleid.com", "booking": "guest.booking.com",
               "agoda": "agoda-messaging.com", "expedia": "m.expediapartnercentral.com"}[kind]
        if kind == "booking":
            rnd = rnd[:6] + "." + str(rng.randint(100000, 999999))
        e = self._unique_email(rnd, dom)
        self._own_email(idx, e)
        return e

    # ---- 이름 -------------------------------------------------------------
    def gen_given(self, sex, by, share_with=None, avoid=None):
        rng = self.rng
        first, second = _POOL_W[(_era(by), sex)]
        if share_with and len(share_with) == 2:
            for _ in range(30):
                pos = rng.randrange(2)
                cand = share_with[0] + second.pick(rng) if pos == 0 else first.pick(rng) + share_with[1]
                if cand != share_with and cand != avoid and cand[0] != cand[1]:
                    return cand
        for _ in range(30):
            if rng.random() < P_POPULAR_NAME:
                g = _POPULAR_W[(_decade(by), sex)].pick(rng)
            elif rng.random() < P_ONE_SYLLABLE:
                g = _ONE_SYL_W[sex].pick(rng)
            else:
                a, b = first.pick(rng), second.pick(rng)
                if a == b:
                    continue
                g = a + b
            if g != share_with and g != avoid:
                return g
        return g

    # ---- 사람 -------------------------------------------------------------
    def new_korean(self, sex=None, by=None, sur=None, dob=None, given=None, hh=None):
        rng = self.rng
        if dob is not None:
            by = dob.year
        if by is None:
            by = _BIRTH_YEAR_W.pick(rng)
        by = max(1940, min(2006, by))
        if sex is None:
            sex = "M" if rng.random() < 0.5 else "F"
        if dob is None:
            dob = _dt.date(by, 1, 1) + _dt.timedelta(days=rng.randrange(366 if by % 4 == 0 else 365))
        if sur is None:
            sur = _SUR_W.pick(rng)
        if given is None:
            given = self.gen_given(sex, by)
        idx = len(self.P)
        p = {"idx": idx, "foreign": False, "culture": "KR", "sex": sex, "sur_ko": sur, "given_ko": given,
             "dob": dob, "hh": hh if hh is not None else self._new_hh(), "kin": []}
        p["sur_en"] = _SUR_SPELL[sur].pick(rng)                       # 근거4·5: 본인 철자 하나
        p["given_en"] = [_rom_syl(rng, ch, "own") for ch in given]
        p["given_join"] = _W["join_own"].pick(rng)
        p["en_style"] = _W["style_own"].pick(rng)
        p["eng_nick"] = rng.choice(ENG_NICK[sex]) if by >= 1965 and rng.random() < P_ENG_NICK else ""
        p["old_given_ko"] = ""
        if rng.random() < P_NAME_CHANGE:
            p["old_given_ko"] = self.gen_given(sex, by)
            if p["old_given_ko"] == given:
                p["old_given_ko"] = ""
            else:
                p["old_given_en"] = [_rom_syl(rng, ch, "own") for ch in p["old_given_ko"]]
        p["lunar"] = rng.random() < (P_LUNAR_FLAG_OLD if by < 1970 else P_LUNAR_FLAG_YOUNG)
        p["lunar_offset"] = rng.randint(18, 50)
        self.P.append(p)
        p["phone"] = self.new_kr_mobile()
        self._own_phone(idx, p["phone"])
        p["alt_phone"] = self.new_kr_mobile(old_2g=(by <= 1975 and rng.random() < P_OLD_2G_NUMBER))
        self._own_phone(idx, p["alt_phone"])
        p["email"] = self.kr_email(p)
        p["emails_extra"] = {}
        p["ci"] = self.new_ci()
        p["share_email"] = None
        p["share_use"] = 0.0
        p["share_from"] = None
        p["reassigned_to"] = None
        return idx

    def new_foreign(self, culture=None, sex=None, by=None, sur=None, hh=None):
        rng = self.rng
        if culture is None:
            culture = _W["culture"].pick(rng)
        F = FOREIGN[culture]
        if by is None:
            by = _BIRTH_YEAR_W.pick(rng)
        by = max(1940, min(2006, by))
        if sex is None:
            sex = "M" if rng.random() < 0.5 else "F"
        dob = _dt.date(by, 1, 1) + _dt.timedelta(days=rng.randrange(365))
        idx = len(self.P)
        if sur is None:
            sur = _variant_pick(rng, rng.choice(F["sur"]), 0.85)
        given = _variant_pick(rng, rng.choice(F[sex]), 0.85)
        if culture == "CN" and " " in given and rng.random() < 0.6:
            a, b = given.split(" ", 1)
            given = a + b.lower()   # 본인 표기: 붙여 쓰기(Xiaoming)가 다수
        p = {"idx": idx, "foreign": True, "culture": culture, "sex": sex, "sur_ko": "", "given_ko": "",
             "dob": dob, "hh": hh if hh is not None else self._new_hh(), "kin": [],
             "sur_en": sur, "given_fx": given, "middle": "", "eng_first": "",
             "order_sg": rng.random() < F["order_sg"], "ci": "", "share_email": None, "share_use": 0.0,
             "share_from": None, "reassigned_to": None, "eng_nick": "", "old_given_ko": "",
             "lunar": False, "emails_extra": {}}
        if culture == "WEST" and rng.random() < P_WEST_MIDDLE:
            p["middle"] = rng.choice("ABCDEFGHJKLMNPRSTW") + "."
        if culture == "HKTW" and rng.random() < P_HK_ENG_NAME:
            p["eng_first"] = rng.choice(F["eng"][sex])
        self.P.append(p)
        mode = _W["f_phone"].pick(rng)
        p["phone"] = ""
        p["phone_cc"] = ""
        if mode == "foreign":
            cc = _WT(F["cc"]).pick(rng)
            cc, nat = self.new_foreign_phone(cc)
            p["phone"], p["phone_cc"] = nat, cc
            self._own_phone(idx, "+" + cc + nat)
        elif mode == "korean":
            p["phone"] = self.new_kr_mobile()
            self._own_phone(idx, p["phone"])
        p["alt_phone"] = ""
        handle = (given.split()[0] + rng.choice((".", "", "_")) + sur).lower().replace(" ", "").replace("'", "")
        handle = "".join(ch for ch in handle if ch.isascii())
        if rng.random() < 0.5:
            handle += str(rng.randint(1, 9999))
        p["email"] = self._unique_email(handle, _WT(F["domains"]).pick(rng))
        self._own_email(idx, p["email"])
        return idx

    def _new_hh(self):
        self.hh_count += 1
        return self.hh_count

    # ---- 가구 -------------------------------------------------------------
    def build_korean_household(self, room):
        rng = self.rng
        a = self.new_korean()
        A = self.P[a]
        hh = A["hh"]
        roles = {a: "A"}
        age = _age(A["dob"].year)
        if room - len(roles) > 0 and rng.random() < P_TWIN_ANCHOR:
            sex = A["sex"] if rng.random() < P_TWIN_SAME_SEX else ("F" if A["sex"] == "M" else "M")
            share = A["given_ko"] if rng.random() < P_TWIN_SHARE_SYLLABLE else None
            given = self.gen_given(sex, A["dob"].year, share, avoid=A["given_ko"])
            t = self.new_korean(sex=sex, sur=A["sur_ko"], dob=A["dob"], given=given, hh=hh)
            roles[t] = "T"
        spouse = None
        if room - len(roles) > 0 and rng.random() < _by_age(P_MARRIED_BY_AGE, age) * P_SPOUSE_IN_DATA:
            off = int(round(rng.gauss(2.5, 2.5)))
            by = A["dob"].year + (off if A["sex"] == "M" else -off)
            spouse = self.new_korean(sex="F" if A["sex"] == "M" else "M", by=by, hh=hh)  # 성은 독립 추첨(혼인해도 안 바뀜)
            roles[spouse] = "S"
        if room - len(roles) > 0 and age >= 48 and rng.random() < P_CHILD_IN_DATA:
            cby = A["dob"].year + rng.randint(26, 36)
            if cby <= 2006:
                if A["sex"] == "M":
                    csur = A["sur_ko"]
                elif spouse is not None:
                    csur = self.P[spouse]["sur_ko"]
                else:
                    csur = _SUR_W.pick(rng)   # 아버지는 DB에 없음
                c = self.new_korean(by=cby, sur=csur, hh=hh)
                roles[c] = "C"
        if room - len(roles) > 0 and rng.random() < P_SIBLING_IN_DATA:
            sby = A["dob"].year + rng.choice((-1, 1)) * rng.randint(1, 6)
            sex = "M" if rng.random() < 0.5 else "F"
            share = A["given_ko"] if rng.random() < P_SIBLING_SHARE_SYLLABLE and sex == A["sex"] else None
            given = self.gen_given(sex, max(1940, min(2006, sby)), share, avoid=A["given_ko"])
            b = self.new_korean(sex=sex, by=sby, sur=A["sur_ko"], given=given, hh=hh)
            roles[b] = "B"
        self._link_household(roles)
        members = list(roles)
        # 일부러 심는 동명이인·생일 같음(다른 가구)
        if room - len(members) > 0 and rng.random() < P_HOMONYM_SAME_DOB:
            h = self.new_korean(sex=A["sex"], sur=A["sur_ko"], given=A["given_ko"], dob=A["dob"])
            self.link(a, h, "동명이인·생일 같음")
            members.append(h)
        return members

    def _link_household(self, roles):
        P = self.P
        ids = list(roles)
        blood = {"A", "T", "C", "B"}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                x, y = ids[i], ids[j]
                rx, ry = roles[x], roles[y]
                pair = {rx, ry}
                if pair == {"A", "S"}:
                    t = "배우자"
                elif pair == {"A", "T"}:
                    t = "쌍둥이"
                elif "S" in pair and (pair - {"S"}) <= {"T", "B"}:
                    t = "인척"
                else:  # 혈연(부모·자녀·형제·삼촌-조카). S-C는 자녀로 간주
                    t = "가족(같은 성)" if P[x]["sur_ko"] == P[y]["sur_ko"] else "가족(다른 성)"
                self.link(x, y, t)
                P[x]["kin"].append(y)
                P[y]["kin"].append(x)
        self._share_emails(roles)

    def _share_emails(self, roles):
        """근거8: 배우자 공유는 Pew 2014 나이대별, 가족은 브리프 지정값."""
        rng, P, lvl = self.rng, self.P, self.sc["email_share_level"]
        inv = {}
        for k, r in roles.items():
            inv.setdefault(r, []).append(k)
        a = inv["A"][0]
        if "S" in inv:
            s = inv["S"][0]
            age = _age(min(P[a]["dob"].year, P[s]["dob"].year))
            p = _by_age(SPOUSE_SHARE_BASE_BY_AGE, age) if lvl == "base" else SPOUSE_SHARE_FLAT[lvl]
            if rng.random() < p:
                owner, user = (a, s) if rng.random() < 0.5 else (s, a)
                self._set_share(user, owner, SHARED_EMAIL_USE)
        pf = FAMILY_SHARE[lvl]
        young = [(k, r) for k, r in roles.items() if r in ("C", "B", "T")]
        for k, r in young:
            if rng.random() >= pf:
                continue
            if r == "C":
                elders = [x for x in inv.get("A", []) + inv.get("S", [])]
            else:
                elders = [a]
            owner = rng.choice(elders)
            if r in ("B", "T") and P[k]["dob"] < P[a]["dob"]:
                owner, k = k, owner  # 손위가 주소 주인
            if P[k]["share_email"] is None and P[owner]["share_from"] != k:
                self._set_share(k, owner, FAMILY_EMAIL_USE)

    def _set_share(self, user, owner, use):
        P = self.P
        src = P[owner]["share_email"] or P[owner]["email"]
        if P[user]["share_email"] is None and src != P[user]["email"]:
            P[user]["share_email"] = src
            P[user]["share_use"] = use
            P[user]["share_from"] = owner

    def build_foreign_household(self, room):
        rng = self.rng
        a = self.new_foreign()
        A = self.P[a]
        members = [a]
        if room > 1 and rng.random() < P_FOREIGN_SPOUSE:
            off = int(round(rng.gauss(2.5, 2.5)))
            by = A["dob"].year + (off if A["sex"] == "M" else -off)
            sur = None
            if A["culture"] in ("JP", "WEST", "KDIAS") and rng.random() < P_FOREIGN_SPOUSE_SAME_SUR:
                sur = A["sur_en"]
            s = self.new_foreign(culture=A["culture"], sex="F" if A["sex"] == "M" else "M", by=by, sur=sur, hh=A["hh"])
            self.link(a, s, "배우자")
            A["kin"].append(s)
            self.P[s]["kin"].append(a)
            members.append(s)
            age = _age(min(A["dob"].year, self.P[s]["dob"].year))
            lvl = self.sc["email_share_level"]
            p = _by_age(SPOUSE_SHARE_BASE_BY_AGE, age) if lvl == "base" else SPOUSE_SHARE_FLAT[lvl]
            if rng.random() < p:
                owner, user = (a, s) if rng.random() < 0.5 else (s, a)
                self._set_share(user, owner, SHARED_EMAIL_USE)
        return members

    # ---- 레코드 도우미 ------------------------------------------------------
    def kr_names(self, p, allow_old):
        """(한글명, 본인 영문 음절, 한글 이름) — 개명자는 옛 이름일 수 있다."""
        if allow_old and p["old_given_ko"] and self.rng.random() < P_NAME_CHANGE_OLD_ON_REC:
            return p["sur_ko"] + p["old_given_ko"], p["old_given_en"], p["old_given_ko"]
        return p["sur_ko"] + p["given_ko"], p["given_en"], p["given_ko"]

    def ko_typed(self, full, sur):
        if self.rng.random() < P_KO_SPACE:
            return sur + " " + full[len(sur):]
        return full

    def en_own(self, p, syl, style=None, given_ko=None):
        rng = self.rng
        syl = list(syl)
        join = p["given_join"]
        if rng.random() < P_EN_DEVIATION:
            if rng.random() < 0.5 and syl:
                k = rng.randrange(len(syl))
                g = given_ko or p["given_ko"]
                if k < len(g):
                    syl[k] = _rom_syl(rng, g[k], "own")
            else:
                join = _W["join_own"].pick(rng)
        if style is None:
            style = p["en_style"] if rng.random() < P_KEEP_OWN_STYLE else _W["style_own"].pick(rng)
        return _render_en(p["sur_en"], _join_given(syl, join), style)

    def en_desk(self, p, given_ko):
        rng = self.rng
        mode = "rr" if rng.random() < P_DESK_RR else "modal"
        sur = _SUR_SPELL[p["sur_ko"]].modal()
        if mode == "rr" and p["sur_ko"] != "이" and rng.random() < 0.3:   # 성까지 RR로(박→Bak, 정→Jeong)
            sur = "".join(_rom_syl(rng, ch, "rr") for ch in p["sur_ko"]).capitalize()
        syl = [_rom_syl(rng, ch, mode) for ch in given_ko]
        return _render_en(sur, _join_given(syl, _W["join_desk"].pick(rng)), _W["style_desk"].pick(rng))

    def email_used(self, p, own_only=False):
        if not own_only and p["share_email"] and self.rng.random() < p["share_use"]:
            return p["share_email"]
        return p["email"]

    def case_var(self, e):
        rng = self.rng
        if e and rng.random() < P_EMAIL_CASE_VARIANT:
            return e.upper() if rng.random() < 0.3 else e[0].upper() + e[1:]
        return e

    def extra_email(self, p, provider):
        """SNS 제공자 계정 주소. 근거2(카카오·네이버 메일이 흔함)."""
        rng = self.rng
        dom = {"naver": "naver.com", "google": "gmail.com", "kakao": "kakao.com"}[provider]
        if p["email"].endswith("@" + dom):
            return p["email"]
        if provider == "kakao" and rng.random() < 0.5 and "kakao" not in p["emails_extra"]:
            p["emails_extra"]["kakao"] = p["email"]   # 카카오 계정을 기존 메일로 만든 경우
        if provider not in p["emails_extra"]:
            p["emails_extra"][provider] = self.kr_email(p, dom)
        return p["emails_extra"][provider]

    def self_dob(self, p):
        """직접 대는 생일: 음력파는 음력으로 적기도 한다(가정)."""
        d = p["dob"]
        if p["lunar"] and self.rng.random() < P_LUNAR_ON_SELF_DOB:
            d = d - _dt.timedelta(days=p["lunar_offset"])
        return d.isoformat()

    def phone_digits(self, p, is_ref, own_only=False):
        if p["foreign"] or own_only:
            return p["phone"]
        if not is_ref and self.rng.random() < self.sc["phone_change"]:
            if p["reassigned_to"] is not None:
                self.link(p["idx"], p["reassigned_to"], "번호 재할당(타인)")
            return p["alt_phone"]
        return p["phone"]

    def pick_booker(self, p):
        """비회원 투숙의 연락처 주인: 본인 / 가족 / 지인."""
        rng = self.rng
        kin = [k for k in p["kin"] if not self.P[k]["foreign"] or p["foreign"]]
        if kin and rng.random() < P_STAY_BOOKED_BY_KIN:
            return self.P[rng.choice(kin)]
        if not p["foreign"] and rng.random() < P_STAY_BOOKED_BY_FRIEND and p["idx"] not in self.friend_used:
            for _ in range(10):
                y = rng.randrange(len(self.P))
                Y = self.P[y]
                # 예약해 준 지인은 DB 안 가족·번호 재할당이 없는 사람으로(그 값을 쓰는 제3자가 생기지 않게)
                if (y != p["idx"] and not Y["foreign"] and Y["hh"] != p["hh"] and not Y["kin"]
                        and Y["reassigned_to"] is None and y not in self.reassign_targets
                        and y not in self.friend_used and not self.linked(y, p["idx"])):
                    self.friend_used.update((y, p["idx"]))
                    self.link(p["idx"], y, "지인 대리예약")
                    return Y
        return p

    # ---- 레코드 종류별 ------------------------------------------------------
    def rec_online(self, p, is_ref):
        name_ko, syl, given_ko = self.kr_names(p, False)
        return {"kind": "online_member", "name_ko": name_ko, "name_en": self.en_own(p, syl, None, given_ko),
                "phone": _fmt_kr(self.phone_digits(p, is_ref), "dash"),
                "email": self.case_var(self.email_used(p)), "dob": p["dob"].isoformat(), "ci": p["ci"]}

    def rec_offline(self, p, is_ref, with_ci):
        rng = self.rng
        name_ko, syl, given_ko = self.kr_names(p, not with_ci)
        r = {"kind": "offline_member", "name_ko": "", "name_en": "", "phone": "", "email": "", "dob": "",
             "ci": p["ci"] if with_ci else ""}
        if rng.random() < OFFLINE_P["name_ko"]:
            r["name_ko"] = self.ko_typed(name_ko, p["sur_ko"])
        if rng.random() < OFFLINE_P["name_en"] or not r["name_ko"]:
            r["name_en"] = self.en_own(p, syl, None, given_ko) if rng.random() < P_OFFLINE_EN_OWN else self.en_desk(p, given_ko)
        if rng.random() < OFFLINE_P["phone"]:
            r["phone"] = _fmt_kr(self.phone_digits(p, is_ref), _W["fmt_off"].pick(rng))
        if rng.random() < OFFLINE_P["email"]:
            r["email"] = self.case_var(self.email_used(p))
        if with_ci:
            r["dob"] = p["dob"].isoformat()   # 본인인증으로 채워진 생일
        elif rng.random() < OFFLINE_P["dob"]:
            r["dob"] = "1900-01-01" if rng.random() < P_DOB_PLACEHOLDER else self.self_dob(p)
        return r

    def rec_sns(self, p, is_ref):
        rng = self.rng
        prov = _W["sns"].pick(rng)
        name_ko, syl, given_ko = self.kr_names(p, True)
        r = {"kind": "sns_member", "name_ko": "", "name_en": "", "phone": "", "email": "", "dob": "", "ci": ""}
        if prov == "kakao":
            x = rng.random()
            if x < 0.75:
                r["name_ko"] = name_ko
            elif x < 0.85:
                r["name_ko"] = given_ko                       # 이름만
            elif x < 0.90 and 30 <= _age(p["dob"].year) <= 55:
                r["name_ko"] = given_ko[-1] + "이" + rng.choice(("맘", "아빠")) if len(given_ko) else name_ko
            elif x < 0.93:
                r["name_ko"] = rng.choice(KAKAO_NICKS)       # 별명 — 실명이 아님
            else:
                r["name_en"] = (p["eng_nick"] or _join_given(syl, p["given_join"]))  # 라틴 문자 프로필명
            r["email"] = self.extra_email(p, "kakao")
            if rng.random() < 0.35:
                r["phone"] = _fmt_kr(self.phone_digits(p, is_ref), "intl_dash")  # 가정: 카카오 응답 형식 +82 10-…
            if rng.random() < 0.25:
                r["dob"] = p["dob"].isoformat()
        elif prov == "naver":
            r["name_ko"] = name_ko if rng.random() < 0.95 else given_ko
            r["email"] = self.extra_email(p, "naver")
            if rng.random() < 0.6:
                r["phone"] = _fmt_kr(self.phone_digits(p, is_ref), "dash")
            if rng.random() < 0.5:
                r["dob"] = p["dob"].isoformat()
        elif prov == "apple":
            if rng.random() < 0.6:
                r["name_ko"] = name_ko
            else:
                r["name_en"] = self.en_own(p, syl, "GS_title", given_ko)
            r["email"] = self.relay_email(p["idx"], "apple") if rng.random() < 0.4 else self.email_used(p)
        else:  # google
            if rng.random() < 0.6:
                nick = p["eng_nick"] if rng.random() < 0.5 else ""
                r["name_en"] = _render_en(p["sur_en"], nick or _join_given(syl, p["given_join"]), "GS_title")
            else:
                r["name_ko"] = name_ko
            r["email"] = self.extra_email(p, "google")
            if rng.random() < 0.1:
                r["dob"] = p["dob"].isoformat()
        return r

    def rec_stay_kr(self, p, is_ref):
        rng = self.rng
        ch = _W["stay"].pick(rng)
        name_ko, syl, given_ko = self.kr_names(p, True)
        bk = self.pick_booker(p)
        fr = bk is not p and bk["idx"] not in p["kin"]   # 지인: 그 사람 본인 번호·메일만(제3자 값이 섞이지 않게)
        if bk is not p and bk["reassigned_to"] is not None:
            fr = True   # 대신 예약한 가족의 옛 번호가 이미 남에게 넘어갔으면 현재 번호를 쓴다(2단계 공유 방지)
        r = {"kind": "non_member_stay", "name_ko": "", "name_en": "", "phone": "", "email": "", "dob": "", "ci": ""}
        if ch == "domestic_ota":
            r["name_ko"] = name_ko if rng.random() < 0.95 else given_ko
            if rng.random() < P_SAFE_NUMBER_DOMESTIC:
                d = self.new_safe_number()
                self._own_phone(p["idx"], d)
                r["phone"] = _fmt_kr(d, "dash")
            else:
                r["phone"] = _fmt_kr(self.phone_digits(bk, bk is p and is_ref, fr), _W["fmt_direct"].pick(rng))
        elif ch == "global_ota":
            nick = p["eng_nick"] and rng.random() < 0.4
            if nick:
                r["name_en"] = _render_en(p["sur_en"], p["eng_nick"], _W["style_ota"].pick(rng))
            elif rng.random() < 0.88:
                r["name_en"] = self.en_own(p, syl, _W["style_ota"].pick(rng) if rng.random() < 0.6 else None, given_ko)
            else:
                r["name_en"] = self.en_desk(p, given_ko)       # 예약자가 다르게 옮김
            if rng.random() < GLOBAL_OTA_P["name_ko"]:
                r["name_ko"] = name_ko
            if rng.random() < GLOBAL_OTA_P["phone"]:
                r["phone"] = _fmt_kr(self.phone_digits(bk, bk is p and is_ref, fr), _W["fmt_ota"].pick(rng))
            x = rng.random()
            if x < P_OTA_RELAY_EMAIL:
                r["email"] = self.relay_email(p["idx"], rng.choice(("booking", "agoda", "expedia")))
            elif x < P_OTA_RELAY_EMAIL + P_OTA_REAL_EMAIL:
                r["email"] = self.case_var(self.email_used(bk, fr))
            if rng.random() < GLOBAL_OTA_P["dob"]:
                r["dob"] = p["dob"].isoformat()
        else:  # direct: 전화·워크인 비회원
            if rng.random() < DIRECT_STAY_P["name_ko"]:
                r["name_ko"] = self.ko_typed(name_ko, p["sur_ko"])
            if rng.random() < DIRECT_STAY_P["name_en"] or not r["name_ko"]:
                r["name_en"] = self.en_desk(p, given_ko)
            if rng.random() < DIRECT_STAY_P["phone"]:
                r["phone"] = _fmt_kr(self.phone_digits(bk, bk is p and is_ref, fr), _W["fmt_off"].pick(rng))
            if rng.random() < DIRECT_STAY_P["email"]:
                r["email"] = self.case_var(self.email_used(bk, fr))
            if rng.random() < DIRECT_STAY_P["dob"]:
                r["dob"] = self.self_dob(p)
        if not r["name_ko"] and not r["name_en"]:
            r["name_ko"] = name_ko
        return r

    def f_name(self, p):
        rng = self.rng
        given = p["given_fx"]
        if p["culture"] in ("CN", "HKTW", "SEA") and " " in given and rng.random() < 0.3:
            a, b = given.split(" ", 1)
            given = rng.choice((a + b.lower(), a + "-" + b, a + "-" + b.lower(), given))
        if p["middle"] and rng.random() < P_MIDDLE_ON_REC:
            given = given + " " + p["middle"]
        if p["eng_first"] and rng.random() < P_HK_ENG_ON_REC:
            given = p["eng_first"] + " " + given if rng.random() < 0.7 else p["eng_first"]
        if p["culture"] == "JP" and rng.random() < 0.08:      # 장음 표기 흔들림(Yuko/Yuuko 등)
            given = given.replace("uu", "u").replace("ou", "o") if rng.random() < 0.5 else given
        sg = p["order_sg"] if rng.random() < 0.75 else not p["order_sg"]
        x = rng.random()
        case = "upper" if x < 0.45 else ("lower" if x < 0.5 else "title")
        style = ("SG_" if sg else "GS_") + ("upper" if case == "upper" else "title")
        s = _render_en(p["sur_en"], given, style)
        return s.lower() if case == "lower" else s

    def f_phone(self, p, bk):
        rng = self.rng
        q = bk if bk is not None else p
        if not q["phone"]:
            return ""
        if not q["foreign"] or not q.get("phone_cc"):
            return _fmt_kr(q["phone"], _W["fmt_fkr"].pick(rng))
        cc, nat = q["phone_cc"], q["phone"]
        x = rng.random()
        if x < 0.6:
            if cc == "1":
                return f"+1 ({nat[:3]}) {nat[3:6]}-{nat[6:]}" if rng.random() < .5 else f"+1 {nat[:3]}-{nat[3:6]}-{nat[6:]}"
            return f"+{cc} " + _group(nat, _FOREIGN_GROUPS.get(cc, (3, 3, 4)))
        if x < 0.85:
            return "+" + cc + nat
        return ("0" + nat) if cc not in ("1", "86", "852") else nat     # 자국 국내 형식

    def rec_foreign(self, p, kind):
        rng = self.rng
        r = {"kind": kind, "name_ko": "", "name_en": self.f_name(p), "phone": "", "email": "", "dob": "", "ci": ""}
        if kind == "foreign_member":
            r["phone"] = self.f_phone(p, None) if rng.random() < 0.9 else ""
            if rng.random() < FOREIGN_MEMBER_P["email"]:
                r["email"] = self.case_var(self.email_used(p))
            if rng.random() < FOREIGN_MEMBER_P["dob"]:
                r["dob"] = p["dob"].isoformat()
        else:
            bk = self.pick_booker(p)
            if rng.random() < 0.85:
                r["phone"] = self.f_phone(p, bk)
            x = rng.random()
            if x < P_OTA_RELAY_EMAIL:
                r["email"] = self.relay_email(p["idx"], rng.choice(("booking", "agoda", "expedia")))
            elif x < P_OTA_RELAY_EMAIL + P_OTA_REAL_EMAIL:
                r["email"] = self.case_var(self.email_used(bk))
            if rng.random() < 0.03:
                r["dob"] = p["dob"].isoformat()
        return r

    # ---- 사람별 레코드 묶음 -------------------------------------------------
    def records_for(self, p):
        rng = self.rng
        lvl = self.sc["ci_less_level"]
        out = []
        if p["foreign"]:
            kinds = ["foreign_member" if rng.random() < FOREIGN_FIRST_MEMBER else "non_member_stay"]
            kinds += [_W["f_extra_kind"].pick(rng) for _ in range(_negbin(rng, FOREIGN_EXTRA_MEAN))]
            for k in kinds:
                out.append(self.rec_foreign(p, k))
            return out
        kinds = []
        if rng.random() < P_ONLINE[lvl]:
            kinds.append("online_member")    # 근거1: DI 때문에 최대 1개
        n = _negbin(rng, EXTRA_MEAN[lvl])
        if not kinds and n == 0:
            n = 1
        kinds += [_W["extra_kind"].pick(rng) for _ in range(n)]
        ref = rng.randrange(len(kinds))      # 현재 번호가 확실히 찍히는 레코드 하나
        for i, k in enumerate(kinds):
            is_ref = i == ref
            if k == "online_member":
                out.append(self.rec_online(p, is_ref))
            elif k == "offline_member":
                out.append(self.rec_offline(p, is_ref, rng.random() < OFFLINE_CI[lvl]))
            elif k == "sns_member":
                out.append(self.rec_sns(p, is_ref))
            else:
                out.append(self.rec_stay_kr(p, is_ref))
        return out

    # ---- 오류 주입 ---------------------------------------------------------
    def corrupt(self, r):
        rng = self.rng
        rates = ({a: STRESS_RATE for a in _ATTRS} if self.sc["typo_level"] == "stress"
                 else TYPO_BASE[r["kind"]])
        order = list(_ATTRS)
        rng.shuffle(order)
        n = 0
        for a in order:
            if n >= MAX_CORRUPT_PER_RECORD:
                break
            v = r[a]
            if v and v != "1900-01-01" and rng.random() < rates[a]:
                nv = _CORRUPT[a](rng, v)
                if nv and nv != v:
                    r[a] = nv
                    n += 1

    def guard_shared(self, recs, clean):
        """오타가 우연히 남의 번호·메일과 같아지면 원래 값으로 되돌린다(설계하지 않은 공유 금지)."""
        for attr, norm, owners in (("phone", norm_phone, self.phone_owner), ("email", norm_email, self.email_owner)):
            orphan = {}
            for i, r in enumerate(recs):
                v = norm(r[attr])
                if not v:
                    continue
                ow = owners.get(v)
                if ow is None:
                    orphan.setdefault(v, []).append(i)
                elif r["pid"] not in ow and not any(self.linked(r["pid"], o) for o in ow):
                    r[attr] = clean[i][attr]
            for v, idxs in orphan.items():
                pids = sorted({recs[i]["pid"] for i in idxs})
                if len(pids) > 1 and not all(self.linked(x, y) for k, x in enumerate(pids) for y in pids[k + 1:]):
                    for i in idxs:
                        recs[i][attr] = clean[i][attr]

    # ---- 전체 ---------------------------------------------------------------
    def run(self, base_persons):
        rng = self.rng
        n_for = int(round(base_persons * self.sc["foreign_share"]))
        n_kr = base_persons - n_for
        while len(self.P) < n_kr:
            self.build_korean_household(n_kr - len(self.P))
        while len(self.P) < base_persons:
            self.build_foreign_household(base_persons - len(self.P))
        P = self.P
        # 자연 발생 동명이인·생일 같음도 링크로 남긴다
        groups = {}
        for p in P:
            if not p["foreign"]:
                groups.setdefault((p["sur_ko"] + p["given_ko"], p["dob"]), []).append(p["idx"])
        for ids in groups.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    if not self.linked(ids[i], ids[j]):
                        self.link(ids[i], ids[j], "동명이인·생일 같음")
        # 옛 번호 재할당: i의 옛 번호 = j의 현재 번호
        kr = [p["idx"] for p in P if not p["foreign"]]
        taken = set()
        for i in kr:
            if rng.random() < P_NUMBER_REASSIGNED:
                j = kr[rng.randrange(len(kr))]
                # 새 주인 j는 DB 안 가족이 없는 사람으로(가족이 j 번호로 대신 예약하면 i와 2단계로 이어지므로)
                if j != i and P[j]["hh"] != P[i]["hh"] and j not in taken and i not in taken and not P[j]["kin"]:
                    taken.update((i, j))
                    self.reassign_targets.add(j)
                    old = P[i]["alt_phone"]
                    self.phone_owner[norm_phone(old)].remove(i)
                    P[i]["alt_phone"] = P[j]["phone"]
                    P[i]["reassigned_to"] = j
                    self.phone_owner[norm_phone(P[j]["phone"])].append(i)
        # 레코드
        recs, clean = [], []
        for p in P:
            for r in self.records_for(p):
                r["pid"] = p["idx"]
                clean.append(dict(r))
                self.corrupt(r)
                recs.append(r)
        self.guard_shared(recs, clean)
        # 순서가 정답을 흘리지 않게 사람·레코드 번호를 섞는다
        perm = list(range(len(P)))
        rng.shuffle(perm)
        order = list(range(len(recs)))
        rng.shuffle(order)
        records = []
        for rid, i in enumerate(order):
            r = recs[i]
            records.append({"rid": rid, "pid": perm[r["pid"]], "kind": r["kind"], "name_ko": r["name_ko"],
                            "name_en": r["name_en"], "phone": r["phone"], "email": r["email"],
                            "dob": r["dob"], "ci": r["ci"]})
        persons = [None] * len(P)
        for p in P:
            q = {"pid": perm[p["idx"]], "foreign": p["foreign"], "culture": p["culture"], "sex": p["sex"],
                 "name_ko": (p["sur_ko"] + p["given_ko"]) if not p["foreign"] else "",
                 "surname_ko": p["sur_ko"], "given_ko": p["given_ko"], "old_given_ko": p["old_given_ko"],
                 "dob": p["dob"].isoformat(), "ci": p["ci"], "household": p["hh"],
                 "phones": [x for x in (p["phone"], p["alt_phone"]) if x],
                 "email": p["email"], "emails_extra": sorted(set(p["emails_extra"].values())),
                 "email_shared_from": perm[p["share_from"]] if p["share_from"] is not None else None}
            if p["foreign"]:
                q["name_en"] = _render_en(p["sur_en"], p["given_fx"], "GS_title")
                q["surname_en"] = p["sur_en"]
                if p.get("phone_cc"):
                    q["phones"] = ["+" + p["phone_cc"] + p["phone"]]
            else:
                q["name_en"] = _render_en(p["sur_en"], _join_given(p["given_en"], p["given_join"]), "GS_title")
                q["surname_en"] = p["sur_en"]
                q["eng_nick"] = p["eng_nick"]
            persons[q["pid"]] = q
        links = sorted((min(perm[a], perm[b]), max(perm[a], perm[b]), t) for (a, b), t in self.links.items())
        return persons, links, records


def generate(seed: int, base_persons: int, scenario: dict) -> tuple[list, list, list]:
    """returns (persons, links, records)"""
    return _Gen(seed, scenario).run(int(base_persons))
