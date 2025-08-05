def write_srt(segments, file_path):
    print(f"✅ write_srt() 호출됨 — 저장 경로: {file_path}")
    print(f"총 세그먼트 수: {len(segments)}")

    def format_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            for i, segment in enumerate(segments):
                try:
                    start = format_time(segment.get("start", 0))
                    end = format_time(segment.get("end", 0))
                    text = segment.get("text", "").strip()

                    f.write(f"{i + 1}\n{start} --> {end}\n{text}\n\n")

                except Exception as seg_err:
                    print(f"[ERROR] 세그먼트 {i+1} 저장 실패: {seg_err}")
                    print(f"segment 내용: {segment}")
                    continue  # 오류가 난 세그먼트는 건너뜀

        print("✅ write_srt() 완료")

    except Exception as e:
        print(f"[ERROR] write_srt 전체 실패: {e}")
        raise
