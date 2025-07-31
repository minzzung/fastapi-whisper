from langdetect import detect

def write_srt(segments, file_path):
    print("✅ 최신 write_srt 호출됨")  # 디버깅용

    def format_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    with open(file_path, "w", encoding="utf-8") as f:
        for i, segment in enumerate(segments):
            start = format_time(segment["start"])
            end = format_time(segment["end"])
            text = segment["text"].strip()
            f.write(f"{i + 1}\n{start} --> {end}\n{text}\n\n")

def detect_language(text: str) -> str:
    try:
        return detect(text)
    except:
        return "unknown"
