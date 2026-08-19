# TechInves Watchlist

**Version:** 1.1
**Date:** 2026-08-16
**Scope:** 43 US-listed technology companies, split into 3 cohorts per the revised
cohort structure in `report_scoring_metadology.md` Section 2, plus a `scoring_excluded`
filter layer (see ADR 0005) that removes 3 tickers from financial scoring while keeping
them in the research/news universe.

---

## A. Yazılım & İnternet (12 şirket)

SaaS, kurumsal yazılım, platform, dijital medya — asset-light, yüksek brüt marj,
SBC yoğun finansal imza.

| Ticker | Şirket |
|---|---|
| MSFT | Microsoft |
| ADBE | Adobe |
| CRM | Salesforce |
| NOW | ServiceNow |
| INTU | Intuit |
| WDAY | Workday |
| SNOW | Snowflake |
| META | Meta Platforms |
| GOOGL | Alphabet |
| SHOP | Shopify |
| AMZN | Amazon.com |
| PLTR | Palantir Technologies |

## B. Donanım, Yarı İletken & Uzay (20 şirket)

Çip tasarımı/üretimi, donanım, uydu/uzay teknolojisi, veri merkezi altyapısı —
sermaye yoğun, döngüsel gelir, geniş brüt marj dağılımı. RKLB, ASTS ve SPCX bu
kohortta kalıyor (araştırma evreninin parçası) ama finansal skorlamadan hariç
tutuluyor — bkz. ADR 0005 §5 ve aşağıdaki not.

| Ticker | Şirket |
|---|---|
| AAPL | Apple |
| NVDA | Nvidia |
| AMD | Advanced Micro Devices |
| INTC | Intel |
| QCOM | Qualcomm |
| AVGO | Broadcom |
| TXN | Texas Instruments |
| MU | Micron |
| AMAT | Applied Materials |
| LRCX | Lam Research |
| TSM | Taiwan Semiconductor (ADR) |
| ASML | ASML Holding (ADR) |
| RKLB | Rocket Lab |
| ASTS | AST SpaceMobile |
| SPCX | SpaceX (Space Exploration Technologies Corp.) |
| GLW | Corning |
| CRWV | CoreWeave |
| ARM | Arm Holdings |
| SMCI | Super Micro Computer |
| VRT | Vertiv Holdings |

**scoring_excluded (ADR 0005 §5):** RKLB, ASTS, SPCX stay in this cohort and the
research/news universe, but are dropped from financial-scoring cohort composition —
see `data/watchlist.yaml`'s `scoring_excluded` key and the "Belirsiz" section below.

## C. IT Hizmetleri & Altyapı (11 şirket)

Kurumsal altyapı, bulut/veritabanı platformları, güvenlik, danışmanlık —
emek yoğun veya altyapı-ağırlıklı, orta brüt marj, istikrarlı nakit akışı.

| Ticker | Şirket |
|---|---|
| ACN | Accenture |
| IBM | IBM |
| ORCL | Oracle |
| CSCO | Cisco |
| DELL | Dell Technologies |
| HPE | Hewlett Packard Enterprise |
| NET | Cloudflare |
| DDOG | Datadog |
| CRWD | CrowdStrike |
| FTNT | Fortinet |
| PANW | Palo Alto Networks |

---

## Belirsiz / tartışmalı yerleşimler (kullanıcı onayı gerekli)

Bu bölüm ADR 0005 §5 uyarınca büyük ölçüde kapatıldı: aşağıdaki üç madde artık
kararlaştırılmış durumda, yalnızca kayıt amacıyla tutuluyor.

- **AAPL** — Kohort B'deki yerleşimi 2026-08-14'te D4 ile netleşmişti
  (`REPORT_SPEC.md` §0 D4); bu revizyonda değişmedi, kapalı madde.
- **TSM, ASML** — Kapsam kararlaştırıldı: ikisi de kapsamda kalıyor. TSM zaten
  hem TWD hem USD etiketliyor ve USD rakamları skorlamada kullanılıyor, ek işlem
  gerekmiyor. ASML için EUR-only XBRL kaydı skorlamayı engelliyordu; ADR 0005
  §4 kararı bunu kapsam dışı bırakmak yerine FX dönüşümü uygulamaktı (planın R6
  maddesi). Bu iş [ADR 0007](../decisions/0007-fx-translation-for-non-usd-filers.md)
  ile tamamlandı ve 2026-08-16 doğrulama turunda canlı ölçümle teyit edildi:
  ASML artık `insufficient_data` değil, ECB referans kurlarıyla dönüştürülmüş
  tam metrik setiyle skorlanıyor.
- **RKLB, ASTS, SPCX** — Kararlaştırıldı: watchlist'te (ve araştırma/haber
  akışında) kalıyorlar, finansal skorlamadan hariç tutuluyorlar
  (`scoring_excluded`, ADR 0005 §5). Sık sık negatif kazanç/erken aşama
  profilleri, yüzdelik-tabanlı skorlamayı bilgilendirmekten çok bozuyor —
  kullanıcı kararı. SPCX 12 Haziran 2026'da NASDAQ'ta halka arz oldu.

### Yeni eklenenler (2026-08-16, ADR 0005)

- **AMZN** (Amazon) — Kohort A'ya atandı: AWS'nin yazılım/platform ekonomisi
  piyasanın hisseyi okuma biçiminde baskın, bu da MSFT/GOOGL/META ile
  yüzdelik-karşılaştırılabilirliği koruyor. Gerekçenin tam kaydı için bkz. ADR
  0005 "Resolution (2026-08-16)".
- **PLTR** (Palantir) — Kohort A. Devlet/kurumsal AI yazılımı.
- **CRWV** (CoreWeave) — Kohort B. Saf AI bulut altyapısı.
- **ARM** (Arm Holdings) — Kohort B. Çip IP, mevcut yarı iletken isimlerini
  tamamlıyor.
- **SMCI** (Super Micro Computer) — Kohort B. AI sunucu donanımı.
- **VRT** (Vertiv) — Kohort B. Veri merkezi güç/soğutma — ADR 0006'daki yeni
  veri merkezi enerji makro konusunu doğrudan besliyor.

### Çıkarılanlar (2026-08-16, ADR 0005)

NFLX, UBER, ABNB, SPOT (medya/pazaryeri/mobilite — çekirdek teknoloji
altyapısı/yazılımı değil), HPQ (tüketici PC/yazıcı, azalan ilgi), EPAM (IT
personel/danışmanlık, niş — ACN zaten kapsıyor). Gerekçenin tam kaydı için bkz.
ADR 0005 §1.

Toplam: 43 şirket (12+20+11), bunlardan 3'ü (RKLB, ASTS, SPCX) finansal
skorlamadan hariç, 40'ı skorlamaya dahil. Bu liste `report_scoring_metadology.md`
ile birlikte pipeline'ın watchlist referansı.

---

_Last updated: 2026-08-16_
