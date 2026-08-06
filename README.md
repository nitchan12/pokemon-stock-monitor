# Pokemon MA6 Stock Monitor

โปรแกรม Production-ready สำหรับตรวจสอบสต็อกสินค้า Pokémon TCG MA6 บนเว็บไซต์
Toys"R"Us ประเทศไทย และแจ้งเตือนผ่าน Telegram เมื่อพบสินค้าใหม่ ราคาเปลี่ยนแปลง
สถานะสต็อกเปลี่ยนแปลง หรือสินค้าถูกนำออกจากหน้าค้นหา

พัฒนาด้วย Python 3.12 บน macOS และออกแบบให้ย้ายไปรันบน GitHub Actions ได้
โดยไม่ต้องแก้โค้ดหลัก (ดูหัวข้อ [Future: GitHub Actions](#future-github-actions))

## สารบัญ

- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Telegram Setup](#telegram-setup)
- [Run](#run)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Known Limitations](#known-limitations)
- [Deployment: GitHub Actions](#deployment-github-actions)
- [Future Improvements](#future-improvements)

## Architecture

โปรแกรมทำงานตาม flow เดียวเสมอ (`src/main.py::run_once`):

```
Load Config -> Load State -> Download HTML -> Parse -> Detect Change -> Notify -> Save State -> Exit
```

แต่ละขั้นตอนแยกอยู่คนละโมดูล ตาม Separation of Concerns:

| โมดูล | หน้าที่ | ทำ Network I/O? |
|---|---|---|
| `config.py` | โหลด/validate ค่าจาก `.env` | ไม่ |
| `scraper.py` | ดาวน์โหลด HTML ดิบจาก URL เป้าหมาย (httpx + tenacity retry) | ใช่ |
| `parser.py` | แปลง HTML -> `list[Product]` (BeautifulSoup) | ไม่ |
| `detector.py` | เทียบ Product ใหม่กับ state เดิม -> `list[Event]` (pure function) | ไม่ |
| `storage.py` | โหลด/บันทึก `data/state.json` แบบ atomic + backup | ไม่ (disk เท่านั้น) |
| `notifier.py` | ส่งข้อความแจ้งเตือนผ่าน Telegram Bot API | ใช่ |
| `models.py` | Pydantic models: `Product`, `Availability`, `StoredState` | ไม่ |
| `utils.py` | ฟังก์ชัน format ราคา/เวลา + ตั้งค่า Rich logging | ไม่ |
| `main.py` | เชื่อมทุกโมดูลเข้าด้วยกันตาม flow ด้านบน | เรียกใช้โมดูลอื่น |

`scraper.py` ไม่รู้จัก `parser.py` และ `parser.py` ไม่ทำ network request ใดๆ
— แยกกันเด็ดขาดตามหลัก Clean Architecture เพื่อให้ทดสอบและแก้ไขแต่ละส่วนได้
อิสระจากกัน

## Installation

ต้องมี Python 3.12 ติดตั้งไว้แล้ว

```bash
git clone <repository-url>
cd pokemon-stock-monitor

# สร้างและเปิดใช้งาน virtual environment
python3.12 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# ติดตั้ง dependencies
pip install -r requirements.txt
```

## Configuration

คัดลอก `.env.example` เป็น `.env` แล้วกรอกค่า:

```bash
cp .env.example .env
```

| ตัวแปร | บังคับ | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|---|
| `BOT_TOKEN` | ใช่ | - | Token ของ Telegram Bot (จาก @BotFather) |
| `CHAT_ID` | ใช่ | - | Chat ID ปลายทางที่จะส่งการแจ้งเตือน |
| `REQUEST_TIMEOUT` | ไม่ | `15` | Timeout (วินาที) สำหรับ HTTP request ทั้งหมด |
| `TARGET_URL` | ไม่ | URL ค้นหา MA6 บน toysrus.co.th | เปลี่ยนได้หากต้องการ monitor คำค้นอื่น |

หากไม่ได้ตั้งค่า `BOT_TOKEN` หรือ `CHAT_ID` โปรแกรมจะหยุดทำงานทันทีตั้งแต่ขั้นตอน
"Load Config" พร้อมข้อความแจ้งชัดเจนว่าตัวแปรใดขาดหายไป (จะไม่ไปพังตอนพยายาม
ส่ง Telegram)

## Telegram Setup

1. เปิดแชทกับ [@BotFather](https://t.me/BotFather) ใน Telegram แล้วพิมพ์ `/newbot`
   ทำตามขั้นตอนเพื่อสร้างบอทใหม่ จะได้ **Bot Token** กลับมา (นำไปใส่ใน `BOT_TOKEN`)
2. หา **Chat ID** ปลายทาง — เลือกวิธีใดวิธีหนึ่ง:
   - แชทกับบอทของคุณ 1 ข้อความ แล้วเปิด
     `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` ในเบราว์เซอร์ จะเห็น
     `"chat":{"id": ...}` ในผลลัพธ์
   - หรือแชทกับ [@userinfobot](https://t.me/userinfobot) เพื่อดู Chat ID ของ
     ตัวเอง (สำหรับส่งเข้าแชทส่วนตัว)
   - สำหรับกลุ่ม/ช่อง: เพิ่มบอทเข้ากลุ่ม/ช่องก่อน แล้วดู Chat ID ด้วยวิธีเดียวกัน
     (Chat ID ของกลุ่มมักขึ้นต้นด้วยเครื่องหมายลบ)
3. นำค่าที่ได้ใส่ใน `.env`

## Run

```bash
# รันครั้งเดียวแล้วจบการทำงาน (โหมดนี้ใช้กับ GitHub Actions cron)
python -m src.main

# รันต่อเนื่องแบบตั้งเวลาในเครื่อง (APScheduler, cron expression 5 ช่อง)
python -m src.main --schedule "*/30 * * * *"
```

Exit code ที่โปรแกรมคืนกลับ (ใช้แยกสาเหตุความล้มเหลวได้จาก CI/monitoring):

| Exit code | ความหมาย |
|---|---|
| `0` | สำเร็จ |
| `1` | โหลด Config ไม่สำเร็จ (ขาด `BOT_TOKEN`/`CHAT_ID` หรือค่าไม่ถูกต้อง) |
| `2` | ดาวน์โหลดหน้าเว็บไม่สำเร็จ (timeout/network error/HTTP error) |
| `3` | แยกวิเคราะห์ HTML ไม่สำเร็จ — โครงสร้างหน้าเว็บอาจเปลี่ยนไป (HTML_CHANGED) |
| `4` | บันทึก state ไม่สำเร็จ |

## Project Structure

```
pokemon-stock-monitor/
├── README.md
├── requirements.txt
├── pyproject.toml          # ruff + mypy + pytest + coverage config
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── monitor.yml      # GitHub Actions: cron + secrets + state persistence
├── src/
│   ├── main.py              # orchestration: run_once() + CLI entrypoint
│   ├── config.py             # โหลด/validate .env -> Settings
│   ├── scraper.py            # ดาวน์โหลด HTML (httpx + tenacity retry)
│   ├── parser.py              # HTML -> list[Product] (BeautifulSoup)
│   ├── detector.py            # เทียบ state -> list[Event]
│   ├── notifier.py            # ส่งข้อความ Telegram
│   ├── storage.py             # โหลด/บันทึก state.json แบบ atomic
│   ├── models.py              # Product, Availability, StoredState (pydantic)
│   └── utils.py               # format ราคา/เวลา + ตั้งค่า logging
├── tests/
│   ├── fixtures/               # HTML fixtures ที่ verify กับโครงสร้างจริงของเว็บ
│   ├── test_scraper.py
│   ├── test_parser.py
│   ├── test_detector.py
│   ├── test_storage.py
│   ├── test_notifier.py
│   ├── test_config.py
│   ├── test_utils.py
│   └── test_main.py
└── data/
    └── state.json            # state ล่าสุด (ไม่ถูก track ใน git โดยตั้งใจ)
```

## Testing

```bash
# รันเทสต์ทั้งหมด
pytest

# รันพร้อม coverage report
pytest --cov=src --cov-report=term-missing

# ตรวจ lint และ type
ruff check src/ tests/
mypy src/
```

สถานะล่าสุด: **83/83 เทสต์ผ่าน, coverage รวม 93%** (ทุกไฟล์ใน `src/` อยู่ที่ 84%
ขึ้นไป ผ่านเกณฑ์ขั้นต่ำ 80% ที่กำหนดไว้), `ruff check` และ `mypy` ผ่านสะอาด
(zero errors)

เทสต์ทั้งหมดเป็น unit test ล้วน — ไม่มีการเรียก network จริงแม้แต่ครั้งเดียว
(`scraper`/`notifier` mock `httpx.Client`, `parser` อ่านจาก HTML fixture ใน
`tests/fixtures/`)

## Known Limitations

**การตรวจจับสถานะ "สินค้าหมด" (OUT_OF_STOCK) เป็น best-effort ไม่ใช่ค่าที่ยืนยัน
จากตัวอย่างจริง 100%**

ระหว่างพัฒนา ผมตรวจสอบ DOM จริงของหน้าค้นหา (`.search-results .product-grid
[data-pid]`) และหน้าหมวดหมู่ Pokémon ทั้งหมดบน toysrus.co.th ผ่านเบราว์เซอร์
โดยตรง พบว่า:

- Selector สำหรับ `id` / `name` / `price` / `product_url` / badge
  ("พรีออเดอร์" ฯลฯ) **ยืนยันแล้วจากหน้าเว็บจริง** ไม่ใช่การเดา
- หน้าค้นหา/หมวดหมู่ **ไม่มีสินค้าใดอยู่ในสถานะหมดสต็อกในขณะที่ตรวจสอบ**
  ป้าย (badge) ที่พบมีเพียง "พรีออเดอร์", "สินค้าใหม่", "สินค้าขายดี",
  "สินค้าลดล้างสต๊อก" — ไม่มีตัวอย่าง "สินค้าหมด" ให้ตรวจสอบ selector จริง
- หน้ารายละเอียดสินค้า (PDP) มีองค์ประกอบ `.availability.product-availability`
  / `.availability-msg` แต่เนื้อหาถูกโหลดแบบ async และไม่แสดงข้อความใดๆ แม้รอ
  หลายวินาที จึงไม่สามารถยืนยัน selector ของ PDP ได้เช่นกัน

ดังนั้น `parser._detect_availability()` จึงใช้กลยุทธ์ป้องกันความเสี่ยง
(defensive, multi-signal): ตรวจ CSS class ของ tile + ข้อความ badge เทียบกับ
รายการคำที่เป็นไปได้ (`สินค้าหมด`, `หมดสต็อก`, `out of stock` ฯลฯ) + การมี/ไม่มี
ราคา — หากไม่พบสัญญาณใดเลยจะจัดเป็น `UNKNOWN` แทนที่จะเดาว่า `IN_STOCK`
(ดู docstring ใน `src/parser.py` และ `OUT_OF_STOCK_PHRASES`)

**สิ่งที่ควรทำก่อนใช้งานจริง:** เมื่อสินค้า MA6 มีตัวใดหมดสต็อกจริง ให้ตรวจสอบ
ข้อความแจ้งเตือนที่ได้ — หากสถานะไม่ตรง (เช่นควรเป็น OUT_OF_STOCK แต่ระบบให้เป็น
UNKNOWN) ให้เปิดหน้าเว็บด้วยเบราว์เซอร์ ตรวจสอบ CSS class/ข้อความจริงที่ปรากฏ
แล้วเพิ่มเข้าไปใน `OUT_OF_STOCK_CLASS_TOKENS` / `OUT_OF_STOCK_PHRASES` ใน
`src/parser.py`

**Python เวอร์ชันที่ทดสอบจริง:** สภาพแวดล้อมพัฒนาที่ใช้รันเทสต์มี Python 3.10.12
แม้ target ที่กำหนดไว้คือ 3.12 — โค้ดทั้งหมดหลีกเลี่ยง syntax เฉพาะ 3.11+/3.12
โดยตั้งใจ (เช่น ใช้ `class X(str, Enum)` แทน `StrEnum`, ใช้ `timezone.utc` แทน
`datetime.UTC`) เพื่อให้ทดสอบผ่านได้ทั้งสองเวอร์ชัน แนะนำให้รัน `pytest` อีกครั้ง
บนเครื่องจริงที่มี Python 3.12 ก่อนใช้งาน production เพื่อยืนยันผลเช่นเดียวกัน

## Deployment: GitHub Actions

โค้ด core (ทุกอย่างใน `src/`) ไม่ผูกกับ scheduler ใดๆ — `run_once()` คืน
process exit code ตรงไปตรงมา จึงย้ายไปรันบน GitHub Actions ได้โดยไม่ต้องแก้
โค้ดหลักแม้แต่บรรทัดเดียว ไฟล์ workflow พร้อมใช้งานอยู่ที่
[`.github/workflows/monitor.yml`](.github/workflows/monitor.yml) แล้ว

### ขั้นตอนเปิดใช้งาน

1. Push โปรเจกต์ขึ้น GitHub repository
2. ไปที่ **Settings -> Secrets and variables -> Actions -> New repository secret**
   เพิ่ม 2 ค่า:
   - `BOT_TOKEN` — Telegram Bot Token
   - `CHAT_ID` — Chat ID ปลายทาง
3. ไปที่แท็บ **Actions** เลือก workflow "Pokemon MA6 Stock Monitor" แล้วกด
   **Run workflow** เพื่อทดสอบรันครั้งแรกด้วยมือ
4. หลังจากนั้น workflow จะรันเองตาม cron ที่ตั้งไว้ (ค่าเริ่มต้นทุก 15 นาที)

### รายละเอียดที่ workflow จัดการให้แล้ว

- **State persistence** — runner ของ GitHub Actions เป็น ephemeral (ข้อมูลหาย
  ทุกครั้งที่ job จบ) workflow จึง commit `data/state.json` กลับเข้า repo
  หลังแต่ละรอบ หากไม่ทำ ระบบจะมองว่าทุกสินค้าเป็น "ใหม่" และแจ้งเตือนซ้ำทุกรอบ
- **`git add -f`** — `data/state.json` อยู่ใน `.gitignore` โดยตั้งใจ (กัน state
  ของเครื่อง dev ปนขึ้น repo) ดังนั้นบน Actions ต้องใช้ `-f` เพื่อ override
  มิเช่นนั้นคำสั่ง `git add` จะเงียบไม่ทำอะไรและ state จะไม่ถูกบันทึกเลย
- **`permissions: contents: write`** — จำเป็นสำหรับ push กลับ repo
- **`concurrency` guard** — กันไม่ให้สองรอบทำงานทับกันจน commit ชนกัน
- **`git pull --rebase` ก่อน push** — กันกรณีมี commit อื่นถูก push แทรกระหว่างที่
  job กำลังทำงาน (ทดสอบ scenario นี้แล้ว: state commit จะไปต่อบน commit ของคน
  อื่นโดยไม่ทับงานกัน)
- **ไม่ commit เมื่อ state ไม่เปลี่ยน** — ไม่สร้าง empty commit รกประวัติ
- **ไม่ commit เมื่อ monitor ล้มเหลว** — ขั้นตอน persist จะไม่ทำงานหากขั้นตอน
  ก่อนหน้า exit code ไม่ใช่ 0 (พฤติกรรม default ของ GitHub Actions)

### ข้อจำกัดที่ควรทราบ

- **cron ของ GitHub Actions ไม่ตรงเวลา** — ขั้นต่ำที่ตั้งได้คือ 5 นาที แต่ในทาง
  ปฏิบัติ scheduled workflow มักถูกดีเลย์หลายนาทีถึงหลักสิบนาทีในช่วงที่ระบบมีงาน
  หนาแน่น หากต้องการความแม่นยำระดับนาที ให้ใช้โหมด `--schedule` บนเครื่องตัวเอง
  หรือ VPS แทน
- **ประวัติ commit จะยาวขึ้นเรื่อยๆ** จากการบันทึก state — หากไม่ต้องการ
  พิจารณาเปลี่ยนไปใช้ `actions/cache` แทน (แลกกับความเสี่ยงที่ cache จะถูก evict
  หลังไม่ถูกเรียกใช้ 7 วัน ซึ่งจะทำให้แจ้งเตือนซ้ำหนึ่งรอบ)

## Future Improvements

รายการสิ่งที่ควรพัฒนาต่อ เรียงตามลำดับความสำคัญที่แนะนำ:

1. **ยืนยัน selector ของสถานะ "สินค้าหมด" จากตัวอย่างจริง** (ดู Known
   Limitations) — เป็นความเสี่ยงเดียวที่ยังไม่ผ่านการยืนยัน 100% ในระบบนี้
2. **เพิ่ม integration test แบบ opt-in** ที่ยิง request จริงไปยัง toysrus.co.th
   (skip โดย default, เปิดด้วย env var เช่น `RUN_INTEGRATION_TESTS=1`) เพื่อ
   ตรวจจับการเปลี่ยนโครงสร้างเว็บไซต์เชิงรุกก่อนที่ production run จะล้มเหลว
3. **แจ้งเตือนเมื่อ parser ล้มเหลวซ้ำหลายรอบติดกัน** (เช่นส่ง Telegram แจ้ง
   ผู้ดูแลระบบเมื่อ HTML_CHANGED เกิดขึ้น 3 รอบติดต่อกัน) แทนที่จะเงียบแค่ log
4. **เพิ่ม rate-limit / dedup guard ฝั่ง notifier** เผื่อกรณี Telegram API
   ตอบสนองช้าและมีการรันซ้อนกัน (เช่นใช้ file lock ระหว่าง `run_once` แต่ละรอบ)
5. **รองรับหลายคำค้นหา/สินค้าในไฟล์ config เดียว** หากในอนาคตต้องการ monitor
   สินค้าอื่นนอกจาก MA6 ด้วย
6. **Dashboard หรือ log แบบ persistent** สำหรับดูประวัติการเปลี่ยนแปลงราคา/
   สต็อกย้อนหลัง (ปัจจุบัน `state.json` เก็บเฉพาะสถานะล่าสุด ไม่เก็บประวัติ)
