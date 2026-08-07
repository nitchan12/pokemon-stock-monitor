# Pokemon MA6 Stock Monitor

เฝ้าดูหน้าสินค้า Pokémon TCG MA6 บนเว็บไซต์ Toys"R"Us ประเทศไทย
และแจ้งเตือนผ่าน Telegram **ทันทีที่สินค้าพร้อมให้กดใส่ตะกร้า**

พัฒนาด้วย Python 3.12 บน macOS รันได้ทั้งบน GitHub Actions และบนเครื่องตัวเอง
โดยใช้โค้ดหลักชุดเดียวกัน

## สารบัญ

- [ทำงานอย่างไร](#ทำงานอย่างไร)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Telegram Setup](#telegram-setup)
- [Run](#run)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Deployment: GitHub Actions](#deployment-github-actions)
- [ทางเลือก: รันบนเครื่องตัวเอง](#ทางเลือก-รันบนเครื่องตัวเอง)
- [Known Limitations](#known-limitations)
- [Future Improvements](#future-improvements)

## ทำงานอย่างไร

โปรแกรมจะเข้าไปอ่าน **หน้าสินค้าโดยตรง** แล้วดูว่าปุ่มด้านล่างเป็นแบบไหน

| สถานะ | ปุ่มที่เว็บแสดง | ระบบตีความ | แจ้งเตือน |
|---|---|---|---|
| ไม่มีของ | "ไม่มี — แจ้งเตือนฉันเมื่อมีของกลับมาพร้อมจำหน่าย" | `OUT_OF_STOCK` | ไม่แจ้ง |
| เปิดสั่งจอง | "สั่งของล่วงหน้า" (ปุ่มส้ม) | `IN_STOCK` | **แจ้งทันที** |
| มีของพร้อมส่ง | "เพิ่มสินค้าไปยังรถเข็น" | `IN_STOCK` | **แจ้งทันที** |
| อ่านไม่ชัดเจน | สัญญาณขัดแย้งกันเอง | `UNKNOWN` | ไม่แจ้ง (โดยตั้งใจ) |

**"เปิดสั่งจอง" กับ "มีของพร้อมส่ง" ใช้ markup เหมือนกันทุกอย่าง** ต่างแค่ข้อความบนปุ่ม
ทั้งคู่หมายถึง "กดสั่งซื้อได้ตอนนี้" จึงแจ้งเตือนเหมือนกัน แต่ระบบจะเก็บข้อความบนปุ่ม
มาแสดงในแจ้งเตือนด้วย เพื่อให้รู้ว่ากำลังจะสั่งแบบไหน

**นโยบายการแจ้งเตือนซ้ำ** — เมื่อสินค้ากลับมามีของ ระบบจะแจ้งทันที 1 ครั้ง
จากนั้นแจ้งซ้ำได้อีกโดยห่างกันอย่างน้อย 10 นาที รวมสูงสุด 3 ครั้งต่อการกลับมา
มีของหนึ่งรอบ แล้วจะเงียบ เพื่อไม่ให้สแปมหากสินค้ามีขายต่อเนื่องหลายชั่วโมง
ตัวนับจะรีเซ็ตทันทีที่สินค้าหมดอีกครั้ง รอบหน้าจึงแจ้งใหม่ตั้งแต่ต้น
(ปรับได้ด้วย `MAX_NOTIFY_COUNT` และ `REPEAT_INTERVAL_MINUTES`)

โปรแกรม **ไม่แจ้ง** เรื่องราคาเปลี่ยน สินค้าใหม่ หรือสินค้าหมด — ตั้งใจให้เงียบ
ที่สุด แจ้งเฉพาะสิ่งเดียวที่ต้องรีบทำอะไรบางอย่าง

## Architecture

Flow ของการรัน 1 รอบ (`src/main.py::run_once`):

```
Load Config -> Load State -> [ทีละ URL: Download -> Parse] -> Detect (มีของไหม?)
  -> Notify -> Save State -> Exit
```

| โมดูล | หน้าที่ | ทำ Network I/O? |
|---|---|---|
| `config.py` | โหลด/validate ค่าจาก `.env` | ไม่ |
| `scraper.py` | ดาวน์โหลด HTML ดิบ (httpx + tenacity retry) | ใช่ |
| `parser.py` | แปลง HTML หน้าสินค้า -> `Product` | ไม่ |
| `detector.py` | ตัดสินว่าควรแจ้งไหม + คุมการแจ้งซ้ำ (pure function) | ไม่ |
| `storage.py` | โหลด/บันทึก `data/state.json` แบบ atomic + backup | ไม่ (disk) |
| `notifier.py` | ส่งข้อความผ่าน Telegram Bot API | ใช่ |
| `models.py` | Pydantic models: `Product`, `ProductState`, `StoredState` | ไม่ |
| `utils.py` | format ราคา/เวลา + ตั้งค่า Rich logging | ไม่ |
| `main.py` | เชื่อมทุกโมดูลตาม flow ด้านบน | เรียกโมดูลอื่น |

`parser.py` ไม่ทำ network request ใดๆ และ `detector.py` เป็น pure function ที่รับ
`now` เข้ามาเป็นพารามิเตอร์ — ทำให้ทดสอบ logic การแจ้งซ้ำได้โดยไม่ต้อง mock เวลา

**หน้าเว็บไม่ต้องพึ่ง JavaScript** — ยืนยันแล้วว่า markup ของปุ่มอยู่ใน HTML ที่
server ส่งกลับมาตั้งแต่แรก จึงใช้ httpx + BeautifulSoup ได้ ไม่ต้องใช้
Selenium/Playwright

## Installation

ต้องมี Python 3.12

```bash
git clone <repository-url>
cd pokemon-stock-monitor

python3.12 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
```

| ตัวแปร | บังคับ | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|---|
| `BOT_TOKEN` | ใช่ | - | Token ของ Telegram Bot (จาก @BotFather) |
| `CHAT_ID` | ใช่ | - | Chat ID ปลายทาง |
| `REQUEST_TIMEOUT` | ไม่ | `15` | Timeout (วินาที) ของ HTTP request |
| `MAX_NOTIFY_COUNT` | ไม่ | `3` | แจ้งซ้ำได้สูงสุดกี่ครั้งต่อการมีของ 1 รอบ |
| `REPEAT_INTERVAL_MINUTES` | ไม่ | `10` | เว้นระยะขั้นต่ำระหว่างการแจ้งซ้ำ (นาที) |
| `REQUEST_DELAY_SECONDS` | ไม่ | `2` | หน่วงระหว่างยิงแต่ละ URL ในรอบเดียวกัน |
| `PRODUCT_URLS` | ไม่ | URL ของ MA6 ที่ยังมีอยู่ | คั่นด้วยจุลภาค หากต้องการเฝ้าสินค้าอื่น |

หากไม่ได้ตั้ง `BOT_TOKEN` หรือ `CHAT_ID` โปรแกรมจะหยุดทันทีตั้งแต่ขั้นตอนแรก
พร้อมบอกชัดเจนว่าตัวแปรใดขาดหาย

## Telegram Setup

1. เปิดแชทกับ [@BotFather](https://t.me/BotFather) พิมพ์ `/newbot` ทำตามขั้นตอน
   จะได้ **Bot Token** (ใส่ใน `BOT_TOKEN`)
2. หา **Chat ID**: แชทกับบอทของคุณ 1 ข้อความ แล้วเปิด
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` ในเบราว์เซอร์
   หาค่า `"chat":{"id": ...}` ในผลลัพธ์
   - สำหรับกลุ่ม/ช่อง: เพิ่มบอทเข้ากลุ่มก่อน แล้วดูด้วยวิธีเดียวกัน
     (Chat ID ของกลุ่มมักขึ้นต้นด้วยเครื่องหมายลบ)
3. นำค่าที่ได้ใส่ใน `.env`

## Run

```bash
# รันครั้งเดียวแล้วจบ (โหมดที่ GitHub Actions ใช้)
python -m src.main

# รันต่อเนื่องบนเครื่องตัวเอง เช่นทุก 2 นาที
python -m src.main --schedule "*/2 * * * *"
```

Exit code:

| Code | ความหมาย |
|---|---|
| `0` | สำเร็จ |
| `1` | โหลด Config ไม่สำเร็จ |
| `2` | อ่านหน้าสินค้าไม่ได้เลยสักหน้า (ดาวน์โหลดหรือ parse ล้มเหลวทั้งหมด) |
| `4` | บันทึก state ไม่สำเร็จ |

หากมีบางหน้าล้มเหลวแต่ไม่ทั้งหมด โปรแกรมจะ log ไว้แล้วทำงานต่อกับหน้าที่เหลือ
และคืน `0` — เพื่อไม่ให้หน้าเดียวที่พังไปบังการแจ้งเตือนของหน้าอื่นที่ของเข้าพอดี

## Project Structure

```
pokemon-stock-monitor/
├── README.md
├── requirements.txt
├── pyproject.toml            # ruff + mypy + pytest + coverage config
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── monitor.yml        # GitHub Actions: cron + secrets + state persistence
├── src/
│   ├── main.py                # orchestration: run_once() + CLI entrypoint
│   ├── config.py              # โหลด/validate .env -> Settings
│   ├── scraper.py             # ดาวน์โหลด HTML (httpx + tenacity retry)
│   ├── parser.py              # HTML หน้าสินค้า -> Product
│   ├── detector.py            # ตัดสินว่าควรแจ้งไหม + คุมการแจ้งซ้ำ
│   ├── notifier.py            # ส่งข้อความ Telegram
│   ├── storage.py             # โหลด/บันทึก state.json แบบ atomic
│   ├── models.py              # Product, ProductState, StoredState (pydantic)
│   └── utils.py               # format ราคา/เวลา + ตั้งค่า logging
├── tests/
│   ├── fixtures/              # HTML fixtures คัดลอกจาก markup จริงของเว็บ
│   └── test_*.py              # 8 ไฟล์ ครอบคลุมทุกโมดูล
└── data/
    └── state.json             # state ล่าสุด (ไม่ track ใน git โดยตั้งใจ)
```

## Testing

```bash
pytest                                      # รันเทสต์ทั้งหมด
pytest --cov=src --cov-report=term-missing  # พร้อม coverage
ruff check src/ tests/                      # lint
mypy src/                                   # type check
```

สถานะล่าสุด: **92/92 เทสต์ผ่าน**, coverage เกินเกณฑ์ขั้นต่ำ 80% ที่ตั้งไว้,
`ruff` และ `mypy` สะอาดทั้งคู่

เทสต์ทั้งหมดเป็น unit test ล้วน ไม่มีการเรียก network จริงแม้แต่ครั้งเดียว
(`scraper`/`notifier` mock `httpx.Client`, `parser` อ่านจาก HTML fixture)
โดย fixture ของสถานะ "มีของ" และ "ไม่มีของ" คัดลอกมาจาก raw HTML จริงของเว็บ

## Deployment: GitHub Actions

ไฟล์ workflow พร้อมใช้งานอยู่ที่
[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) แล้ว

### ขั้นตอนเปิดใช้งาน

1. Push โปรเจกต์ขึ้น GitHub repository
2. **Settings → Secrets and variables → Actions → New repository secret**
   เพิ่ม `BOT_TOKEN` และ `CHAT_ID`
3. แท็บ **Actions** → เลือก "Pokemon MA6 Stock Monitor" → **Run workflow**
   เพื่อทดสอบรันครั้งแรกด้วยมือ
4. หลังจากนั้นจะรันเองทุก 5 นาที

### เรื่องค่าใช้จ่ายที่ต้องรู้

repo แบบ **Public** ใช้ Actions ได้ฟรีไม่จำกัด แต่ **Private** บัญชีฟรีได้
2,000 นาที/เดือน ซึ่ง cron ทุก 5 นาที (~8,640 รอบ/เดือน) **เกินโควตาแน่นอน**
หากต้องการใช้ private ต้องลดความถี่ลงมากหรือใช้วิธีรันบนเครื่องตัวเองแทน

### รายละเอียดที่ workflow จัดการให้แล้ว

- **State persistence** — runner เป็น ephemeral (ข้อมูลหายทุกครั้งที่ job จบ)
  workflow จึง commit `data/state.json` กลับเข้า repo หลังแต่ละรอบ ถ้าไม่ทำ
  ตัวนับการแจ้งซ้ำจะรีเซ็ตทุกรอบและระบบจะสแปมทุก 5 นาทีตอนของเข้า
- **`git add -f`** — `data/state.json` อยู่ใน `.gitignore` โดยตั้งใจ จึงต้องใช้
  `-f` เพื่อ override มิเช่นนั้น `git add` จะเงียบไม่ทำอะไรและ state จะไม่ถูกบันทึก
- **`concurrency` guard** — กันสองรอบทำงานทับกันจน commit ชนกัน
- **`git pull --rebase` ก่อน push** — กันกรณีมี commit อื่น push แทรกระหว่างรัน
- **ไม่ commit เมื่อ state ไม่เปลี่ยน** และ **ไม่ commit เมื่อ monitor ล้มเหลว**

### ข้อจำกัดเรื่องเวลา (สำคัญ)

cron ของ GitHub Actions ตั้งได้ถี่สุด 5 นาที และในทางปฏิบัติ **มักดีเลย์จริง
5-20 นาที** ในช่วงที่ระบบมีงานหนาแน่น สำหรับสินค้าที่แย่งกันหนักอย่าง Pokémon
preorder ความหน่วงระดับนี้อาจทำให้พลาดของ หากต้องการความถี่จริงระดับ 2 นาที
ให้ใช้วิธีถัดไปแทน

## ทางเลือก: รันบนเครื่องตัวเอง

ได้ความถี่จริงตามที่ตั้ง ไม่มีดีเลย์แบบ GitHub Actions และไม่มีค่าใช้จ่าย
แลกกับต้องเปิดเครื่องทิ้งไว้

```bash
python -m src.main --schedule "*/2 * * * *"
```

ข้อควรรู้: ต้องเปิด terminal ค้างไว้และตั้งไม่ให้เครื่อง sleep
(`caffeinate -i python -m src.main --schedule "*/2 * * * *"` ช่วยกันเครื่องหลับได้)
หากต้องการให้รันเป็น background service อัตโนมัติ ใช้ `launchd` ของ macOS

การเช็คทุก 2 นาที × 2 URL ≈ 1,440 request/วัน ถือว่าค่อนข้างถี่ หากเจอ HTTP 429
หรือ 403 ให้ลดความถี่ลง — `scraper.py` มี retry แบบ exponential backoff อยู่แล้ว
แต่ไม่ได้ช่วยหากโดนบล็อกถาวร

## การตรวจสอบเมื่อสงสัยว่าระบบเงียบผิดปกติ

หากระบบไม่แจ้งเตือนทั้งที่คิดว่าของน่าจะเข้าแล้ว ให้รันสคริปต์วินิจฉัย:

```bash
python3 diagnose.py
```

จะแสดงทีละ URL ว่าเจอหรือไม่เจอสัญญาณตัวไหนบ้าง และตัดสินเป็นอะไร ไม่ต้องใช้
`.env` และไม่ส่ง Telegram — ใช้แยกได้ว่าเป็นปัญหาที่การดาวน์โหลด, การอ่าน
สัญญาณ, หรือเว็บเปลี่ยนโครงสร้าง

ถ้าเห็น `VERDICT: UNKNOWN` แปลว่าสัญญาณขัดแย้งกันหรือหายไป ให้ดูว่าบรรทัดไหนขึ้น
`NOT FOUND` แล้วเทียบกับตาราง "สัญญาณที่ใช้" ใน docstring ของ `src/parser.py`

## Known Limitations

- **ระบบไม่ตรวจจับสินค้า MA6 ตัวใหม่ที่เพิ่งขึ้นเว็บ** — เฝ้าเฉพาะ URL ที่ระบุไว้
  ใน `DEFAULT_PRODUCT_URLS` เท่านั้น หากมี SKU ใหม่โผล่มา ต้องเพิ่มเอง
- **หน้าสินค้าหายแล้วกลับมาได้** — `10161784` เคยคืนค่า HTTP 410 Gone และหายจาก
  หน้าค้นหาไปพักหนึ่ง แล้วกลับมาพร้อมเปิดสั่งจอง ระบบจึงถือว่า fetch ที่ล้มเหลว
  เป็นเรื่องชั่วคราว (log แล้วข้ามไป) ไม่ตัด URL ทิ้ง
- **สถานะ "มีของพร้อมส่ง" ยังไม่เคยเห็นกับ MA6 ตัวจริง** — fixture ของสถานะนั้น
  คัดลอกมาจากสินค้าตัวอื่นบนเว็บเดียวกันที่ใช้ template เดียวกัน ส่วนสถานะ
  "เปิดสั่งจอง" ยืนยันจาก MA6 ตัวจริงแล้ว
- **ระบบไม่ได้ซื้อของให้** — แจ้งเตือนอย่างเดียว ต้องกดซื้อเอง และสำหรับสินค้าที่
  แย่งกันหนัก เวลาที่ใช้ตั้งแต่ระบบตรวจพบจนคุณเปิดลิงก์อาจไม่ทันอยู่ดี
- **Python เวอร์ชันที่ทดสอบ** — สภาพแวดล้อมที่ใช้รันเทสต์ระหว่างพัฒนาเป็น
  Python 3.10.12 แม้ target คือ 3.12 โค้ดหลีกเลี่ยง syntax เฉพาะ 3.11+ โดยตั้งใจ
  (ใช้ `class X(str, Enum)` แทน `StrEnum`, `timezone.utc` แทน `datetime.UTC`)
  แนะนำให้รัน `pytest` ซ้ำบนเครื่องจริงที่เป็น 3.12 ก่อนใช้งาน production
- **ถ้าเว็บเปลี่ยนโครงสร้าง** ระบบจะรายงาน `UNKNOWN` และเงียบแทนที่จะแจ้งผิด ซึ่ง
  ปลอดภัยกว่า แต่แปลว่าอาจเงียบโดยที่ของเข้าจริง — ดูข้อ 1 ใน Future Improvements

## Future Improvements

1. **แจ้งเตือนผู้ดูแลเมื่อระบบอ่านหน้าเว็บไม่ได้ติดกันหลายรอบ** — ตอนนี้หาก
   โครงสร้างเว็บเปลี่ยน ระบบจะเงียบและมีแต่ log เท่านั้น ควรส่ง Telegram แจ้งเมื่อ
   ได้ `UNKNOWN` หรือ fetch ล้มเหลวติดกันเกิน N รอบ เพื่อไม่ให้เงียบแบบไม่รู้ตัว
2. **คำสั่งถามสถานะผ่านแชท** — พิมพ์ "check" หรือ "มีของไหม" ในแชทแล้วให้บอทไป
   ตรวจเดี๋ยวนั้นและตอบกลับ (ต้องใช้ process ที่รันค้างตลอดเพื่อรับ Telegram
   update จึงใช้ร่วมกับ GitHub Actions ไม่ได้)
3. **เพิ่ม integration test แบบ opt-in** ที่ยิง request จริงไปยัง toysrus.co.th
   (skip โดย default) เพื่อตรวจจับการเปลี่ยนโครงสร้างเว็บเชิงรุก
4. **รองรับหลาย chat/หลายผู้รับ** เผื่อแชร์การแจ้งเตือนให้เพื่อน
5. **บันทึกประวัติสถานะ** เพื่อดูย้อนหลังว่าของเข้าช่วงเวลาไหนบ้าง (ปัจจุบัน
   `state.json` เก็บเฉพาะสถานะล่าสุด)
