# Selenium Vocabulary Scraper

Tool Python dùng Selenium để:

1. Mở một trang gốc.
2. Lấy danh sách link chủ đề từ DOM.
3. Vào từng link chủ đề.
4. Tự bấm `Start` vào màn hình học của chủ đề (đối với flow LangGeek).
5. Cào `word + phonetic + example` cho từng từ.
6. Lưu tăng dần để không mất dữ liệu khi bị ngắt giữa chừng.

## Chạy bản UI

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe selenium_vocab_gui.py
```

Nhập link trực tiếp trên UI, rồi bấm nút bắt đầu.

### UI có sẵn

- Ô nhập link trực tiếp
- Thanh tiến trình thật (`done/total topic`)
- Preview danh sách chủ đề + số lượng từ
- Chọn topic để xem ngay danh sách từ vựng
- Ô chọn `chromedriver.exe` khi chạy offline
- Giao diện nền tối (đen)
- Tạo file log mỗi lần chạy tại `output/logs/run_YYYYMMDD_HHMMSS.log`
- Có thống kê realtime: `Processed / OK / SKIP`
- Có nút mở nhanh file log hiện tại
- Có tùy chọn `Resume` để tiếp tục từ dữ liệu cũ

## Chạy bản dòng lệnh

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe selenium_vocab_tool.py --url "https://example.com" --output output
```

Tắt resume khi muốn cào lại toàn bộ từ đầu:

```bash
.venv\Scripts\python.exe selenium_vocab_tool.py --url "https://example.com" --output output --no-resume
```

Nếu chạy offline (không có mạng) thì thêm:

```bash
.venv\Scripts\python.exe selenium_vocab_tool.py --url "https://example.com" --driver-path "E:/path/chromedriver.exe"
```

## Tùy chọn hữu ích

- `--headless` / `--no-headless`: chạy ẩn hoặc hiện trình duyệt.
- `--topic-selector ".topic a"`: chỉ định CSS selector cho link chủ đề.
- `--vocab-selectors "table td,li,.word"`: chỉ định selector để lấy từ vựng.

Mặc định tool sẽ lấy toàn bộ chủ đề tìm được (không giới hạn số lượng).
Tool cũng tự lọc bớt tiêu đề/từ vựng không phải nhóm ký tự Latin (ưu tiên Anh/Việt).

Trong flow LangGeek, tool ưu tiên lấy đúng danh sách topic card, tự vào `Start`,
đọc tab từ vựng (`1- hello`, `2- goodbye`, ...), rồi cào chi tiết từng từ.

## Cấu trúc thư mục kết quả

Mặc định lưu ở thư mục `output` nằm cùng cấp code.

Với mỗi link đầu vào sẽ tạo **1 thư mục riêng** dựa trên link, ví dụ:

```text
output/
	en_example_com_vocabulary/
		summary.json
		all_topics.json
		all_vocabulary.csv
		01_hello-and-goodbye.csv
		02_food.csv
		...
```

Mỗi file topic chứa các cột:

- `order`
- `word`
- `phonetic`
- `example`

Tool lưu dần file topic trong lúc chạy (`SAVE: ...`) nên nếu bị ngắt, dữ liệu đã cào vẫn còn.
Khi chạy lại với `resume`, tool sẽ đọc file topic đã có để tiếp tục hoặc bỏ qua topic đã đủ dữ liệu.
Nếu số từ cào được nhỏ hơn số từ hiển thị của topic, log sẽ có cảnh báo `WARN: ... thiếu từ` để bạn biết ngay topic nào cần kiểm tra lại.

## Gợi ý

Nếu website có cấu trúc rõ ràng, nên truyền `--topic-selector` và `--vocab-selectors` để lấy dữ liệu sạch hơn.

## Lỗi driver khi không có mạng

Lỗi kiểu `Unable to obtain driver for chrome` nghĩa là máy chưa có driver local và Selenium không tải được do offline.

Cách xử lý:

1. Tải đúng `chromedriver.exe` theo version Chrome.
2. Đặt cùng thư mục code **hoặc** chọn đường dẫn trong UI **hoặc** truyền `--driver-path`.

## Đóng gói thành exe trên Windows

Khuyến nghị dùng script có sẵn vì đã tự xử lý đủ các bước:

1. Kiểm tra `.venv\Scripts\python.exe`
2. Cài dependencies
3. Cài `pyinstaller`
4. Tạo icon (`app_icon.ico`)
5. Build exe
6. Copy file exe ra thư mục gốc project

### Cách nhanh nhất (khuyên dùng)

```bat
build_exe.bat
```

Hoặc nếu đang ở PowerShell:

```powershell
.\build_exe.bat
```

Build xong sẽ có 2 file giống nhau:

- `SelediumVocabularyScraper.exe` (ngay thư mục gốc, double-click chạy luôn)
- `dist\SelediumVocabularyScraper.exe`

### Nếu chưa có môi trường `.venv`

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\build_exe.bat
```

### Build thủ công (khi cần debug)

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pyinstaller
powershell -ExecutionPolicy Bypass -File generate_icon.ps1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name SelediumVocabularyScraper --icon app_icon.ico selenium_vocab_gui.py
copy /Y dist\SelediumVocabularyScraper.exe SelediumVocabularyScraper.exe
```

### Lỗi thường gặp

- Lỗi `Khong tim thay .venv\Scripts\python.exe`: tạo `.venv` theo mục trên rồi chạy lại.
- PowerShell chặn script: chạy bằng `cmd` hoặc dùng `powershell -ExecutionPolicy Bypass -File generate_icon.ps1`.